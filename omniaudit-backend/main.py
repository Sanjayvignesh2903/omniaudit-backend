from fastapi import FastAPI, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
import pytesseract
from PIL import Image
import io
import fitz  
import json
import os
import psycopg2 
from groq import Groq

# --- SECURITY: Pulling keys from Cloud Environment Variables ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
client = Groq(api_key=GROQ_API_KEY)

# --- 1. THE DATABASE FETCHER ---
def fetch_live_rules(category_name: str):
    try:
        # Connects using the secure URL provided by Render/Railway
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("SELECT rules_data FROM audit_rules WHERE category = %s;", (category_name,))
        result = cur.fetchone()
        cur.close()
        conn.close()
        
        if result:
            return result[0] 
    except Exception as e:
        print(f"Database Error (Falling back to defaults): {e}")
    
    if category_name == "restaurant": return {"standard_gst": 0.05, "service_charge_allowed": False}
    if category_name == "rental": return {"max_deposit_multiplier": 2, "cleaning_fee_allowed": False}
    return {}

app = FastAPI(title="OmniAudit API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# --- 2. THE DYNAMIC MATH ENGINES ---
def evaluate_rental_rules(data):
    live_rules = fetch_live_rules("rental")
    max_multiplier = live_rules.get("max_deposit_multiplier", 2)
    findings, verdict, savings = [], "FAIR", 0
    rent, deposit = data.get("monthly_rent", 0), data.get("security_deposit", 0)
    
    if deposit > (rent * max_multiplier):  
        findings.append({"status": "warn", "label": f"High Security Deposit: ${deposit}. Ensure this complies with laws."})
        verdict = "REVIEW"
    if data.get("cleaning_fee", 0) > 0 and not live_rules.get("cleaning_fee_allowed", False):
        findings.append({"status": "warn", "label": f"Mandatory Cleaning Fee: ${data.get('cleaning_fee')}. Often illegal to deduct for standard wear."})
        verdict = "REVIEW"
    if verdict == "FAIR": findings.append({"status": "ok", "label": "No illegal charges found."})
    return verdict, savings, findings

def evaluate_restaurant_rules(data):
    live_rules = fetch_live_rules("restaurant")
    current_gst = live_rules.get("standard_gst", 0.05)
    findings, verdict, savings = [], "FAIR", 0
    subtotal, cgst, sgst, service_charge, total = data.get("subtotal", 0), data.get("cgst", 0), data.get("sgst", 0), data.get("service_charge", 0), data.get("total_amount", 0)

    if service_charge > 0 and not live_rules.get("service_charge_allowed", False):
        findings.append({"status": "warn", "label": f"Service Charge: ₹{service_charge}. This is legally optional."})
        verdict, savings = "REVIEW", savings + service_charge
    
    expected_tax = subtotal * current_gst
    actual_tax = cgst + sgst
    if actual_tax > (expected_tax + 1.0): 
        findings.append({"status": "error", "label": f"GST Overcharge! Expected ₹{expected_tax}, but charged ₹{actual_tax}."})
        verdict = "OVERCHARGED"
        
    calc_total = subtotal + cgst + sgst + service_charge
    if abs(calc_total - total) > 1.0: 
        findings.append({"status": "error", "label": f"Math Error! Items sum to ₹{calc_total}, but charged ₹{total}."})
        verdict = "OVERCHARGED"
        
    if verdict == "FAIR": findings.append({"status": "ok", "label": f"Bill math is correct. (Verified against live {current_gst*100}% GST rate)."})
    return verdict, savings, findings

def evaluate_retail_rules(data):
    findings, verdict, savings = [], "FAIR", 0
    if data.get("carry_bag_fee", 0) > 0:
        findings.append({"status": "warn", "label": f"Bag Fee: ₹{data.get('carry_bag_fee')}. Illegal if bag has a brand logo."})
        verdict, savings = "REVIEW", savings + data.get("carry_bag_fee")
    for item in data.get("items_purchased", []):
        if item.get("sale_price", 0) > item.get("mrp", 0):
            findings.append({"status": "error", "label": f"MRP Violation: '{item['name']}' sold above printed MRP."})
            verdict, savings = "OVERCHARGED", savings + (item['sale_price'] - item['mrp'])
    if verdict == "FAIR": findings.append({"status": "ok", "label": "All items priced fairly."})
    return verdict, savings, findings

def evaluate_electricity_rules(data):
    findings, verdict, savings = [], "FAIR", 0
    calc_total = data.get("fixed_charges", 0) + data.get("energy_charges", 0) + data.get("taxes", 0)
    if abs(calc_total - data.get("total_amount", 0)) > 1.0:
        findings.append({"status": "error", "label": f"Calculation Error in Electricity Bill."})
        verdict = "REVIEW"
    else: findings.append({"status": "ok", "label": "Electricity math checks out."})
    return verdict, savings, findings

def evaluate_travel_hotel_rules(data):
    findings, verdict, savings = [], "FAIR", 0
    if data.get("convenience_fee", 0) > 0:
        findings.append({"status": "warn", "label": f"Convenience/Platform Fee: ₹{data.get('convenience_fee')}. Check if this was disclosed."})
        verdict = "REVIEW"
    else: findings.append({"status": "ok", "label": "No hidden convenience fees found."})
    return verdict, savings, findings

# --- 3. THE AI BRAIN & GATEKEEPER ---
def extract_financial_data(raw_text: str, category: str):
    print(f"Sending text to Llama-3.1 for classification and extraction...")
    cat = category.lower()
    
    if cat == "rental": schema = """"landlord_name": "str", "monthly_rent": 0.0, "security_deposit": 0.0, "cleaning_fee": 0.0"""
    elif cat == "restaurant": schema = """"restaurant_name": "str", "subtotal": 0.0, "cgst": 0.0, "sgst": 0.0, "service_charge": 0.0, "total_amount": 0.0"""
    elif cat in ["grocery", "pharmacy", "retail"]: schema = """"store_name": "str", "items_purchased": [{"name": "str", "mrp": 0.0, "sale_price": 0.0}], "carry_bag_fee": 0.0, "total_amount": 0.0"""
    elif cat == "electricity": schema = """"provider_name": "str", "units_consumed_kwh": 0.0, "fixed_charges": 0.0, "energy_charges": 0.0, "taxes": 0.0, "total_amount": 0.0"""
    else: schema = """"vendor_name": "str", "subtotal": 0.0, "taxes": 0.0, "convenience_fee": 0.0, "total_amount": 0.0"""

    system_prompt = f"""
    You are a FinTech AI Gatekeeper and Auditor. The user claims this OCR text is a '{category}' document.
    STEP 1: Verify the category. Does it actually look like a {category} document?
    STEP 2: Extract the data.
    Return STRICTLY a JSON object:
    {{
        "is_correct_category": true or false,
        "suggested_correct_category": "If false, guess the correct category. If true, put null",
        "extracted_data": {{{schema}}}
    }}
    """
    
    chat_completion = client.chat.completions.create(
        messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": f"Raw OCR Text:\n{raw_text}"}],
        model="llama-3.1-8b-instant", response_format={"type": "json_object"}, temperature=0 
    )
    return json.loads(chat_completion.choices[0].message.content)

# --- 4. THE API ROUTES ---
@app.post("/analyze-document/")
async def analyze_document(category: str = Form(...), file: UploadFile = File(...)):
    try:
        image_bytes = await file.read()
        raw_text = ""
        
        if file.filename.lower().endswith('.pdf'):
            pdf_document = fitz.open(stream=image_bytes, filetype="pdf")
            for page_num in range(len(pdf_document)):
                page = pdf_document.load_page(page_num)
                pix = page.get_pixmap()
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                raw_text += pytesseract.image_to_string(img) + "\n"
            pdf_document.close()
        else:
            image = Image.open(io.BytesIO(image_bytes))
            raw_text = pytesseract.image_to_string(image)

        if not raw_text.strip(): return {"verdict": "error", "message": "No text found in the image."}

        ai_response = extract_financial_data(raw_text, category)
        
        if not ai_response.get("is_correct_category", True):
            suggestion = ai_response.get("suggested_correct_category", "different")
            return {"verdict": "error", "message": f"Incorrect Upload: This looks like a {suggestion} bill, but you uploaded it in the {category} section."}

        structured_data = ai_response.get("extracted_data", {})
        cat = category.lower()

        if cat == "rental": verdict, savings, findings = evaluate_rental_rules(structured_data)
        elif cat == "restaurant": verdict, savings, findings = evaluate_restaurant_rules(structured_data)
        elif cat in ["grocery", "pharmacy", "retail"]: verdict, savings, findings = evaluate_retail_rules(structured_data)
        elif cat == "electricity": verdict, savings, findings = evaluate_electricity_rules(structured_data)
        else: verdict, savings, findings = evaluate_travel_hotel_rules(structured_data)

        return {"verdict": verdict, "savings": savings, "extracted_data": structured_data, "findings": findings}

    except Exception as e:
        print(f"Error: {e}")
        return {"verdict": "error", "message": str(e)}