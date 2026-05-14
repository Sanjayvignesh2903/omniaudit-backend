from fastapi import FastAPI, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all websites (like your localhost) to connect
    allow_credentials=True,
    allow_methods=["*"],  # Allows POST, GET, etc.
    allow_headers=["*"],  # Allows all headers
)
import pytesseract
from PIL import Image
import io
import fitz  
import json
import os
import psycopg2 
from groq import Groq
import uvicorn

# --- CONFIGURATION ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
# Use the PORT environment variable provided by Railway
PORT = int(os.getenv("PORT", 8000))

client = Groq(api_key=GROQ_API_KEY)

# --- 1. THE DATABASE FETCHER ---
def fetch_live_rules(category_name: str):
    """Fetches rules from Neon with a secure connection closure."""
    try:
        # Use a context manager to ensure the connection closes even if it fails
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT rules_data FROM audit_rules WHERE category = %s;", (category_name,))
                result = cur.fetchone()
                if result:
                    return result[0] 
    except Exception as e:
        print(f"⚠️ Neon Connection Hint: Make sure your DATABASE_URL ends with ?sslmode=require")
        print(f"Database Error: {e}")
    
    # Static Fallbacks (Safety net)
    fallbacks = {
        "restaurant": {"standard_gst": 0.05, "service_charge_allowed": False},
        "rental": {"max_deposit_multiplier": 2, "cleaning_fee_allowed": False}
    }
    return fallbacks.get(category_name, {})

app = FastAPI(title="OmniAudit API")

# Setup CORS for your Mobile/Web Frontend
app.add_middleware(
    CORSMiddleware, 
    allow_origins=["*"], 
    allow_credentials=True, 
    allow_methods=["*"], 
    allow_headers=["*"]
)

# --- 2. THE DYNAMIC MATH ENGINES ---
def evaluate_rental_rules(data):
    live_rules = fetch_live_rules("rental")
    max_multiplier = live_rules.get("max_deposit_multiplier", 2)
    findings, verdict, savings = [], "FAIR", 0
    rent, deposit = data.get("monthly_rent", 0), data.get("security_deposit", 0)
    
    if deposit > (rent * max_multiplier):  
        findings.append({"status": "warn", "label": f"High Security Deposit: ₹{deposit}. Exceeds {max_multiplier}x rent limit."})
        verdict = "REVIEW"
    if data.get("cleaning_fee", 0) > 0 and not live_rules.get("cleaning_fee_allowed", False):
        findings.append({"status": "warn", "label": f"Mandatory Cleaning Fee: ₹{data.get('cleaning_fee')}. Check local laws for wear/tear rules."})
        verdict = "REVIEW"
    if verdict == "FAIR": findings.append({"status": "ok", "label": "Lease terms appear standard."})
    return verdict, savings, findings

def evaluate_restaurant_rules(data):
    live_rules = fetch_live_rules("restaurant")
    current_gst = live_rules.get("standard_gst", 0.05)
    findings, verdict, savings = [], "FAIR", 0
    subtotal = data.get("subtotal", 0)
    cgst = data.get("cgst", 0)
    sgst = data.get("sgst", 0)
    service_charge = data.get("service_charge", 0)
    total = data.get("total_amount", 0)

    if service_charge > 0 and not live_rules.get("service_charge_allowed", False):
        findings.append({"status": "warn", "label": f"Service Charge: ₹{service_charge}. This is optional per consumer guidelines."})
        verdict, savings = "REVIEW", savings + service_charge
    
    expected_tax = subtotal * current_gst
    actual_tax = cgst + sgst
    if actual_tax > (expected_tax + 1.5): # Added 1.5 buffer for rounding
        findings.append({"status": "error", "label": f"GST Overcharge! Expected ₹{round(expected_tax, 2)}, but charged ₹{actual_tax}."})
        verdict = "OVERCHARGED"
        
    calc_total = subtotal + cgst + sgst + service_charge
    if abs(calc_total - total) > 2.0: 
        findings.append({"status": "error", "label": f"Math Error! Bill should be ₹{calc_total}, but says ₹{total}."})
        verdict = "OVERCHARGED"
        
    if verdict == "FAIR": findings.append({"status": "ok", "label": f"Verified against live {int(current_gst*100)}% GST rate."})
    return verdict, savings, findings

# ... [Keep your other evaluate functions as they are] ...

def evaluate_retail_rules(data):
    findings, verdict, savings = [], "FAIR", 0
    if data.get("carry_bag_fee", 0) > 0:
        findings.append({"status": "warn", "label": f"Bag Fee: ₹{data.get('carry_bag_fee')}. Illegal if bag is branded."})
        verdict, savings = "REVIEW", savings + data.get("carry_bag_fee")
    for item in data.get("items_purchased", []):
        if item.get("sale_price", 0) > item.get("mrp", 0):
            findings.append({"status": "error", "label": f"MRP Violation: '{item['name']}' sold above MRP."})
            verdict, savings = "OVERCHARGED", savings + (item['sale_price'] - item['mrp'])
    if verdict == "FAIR": findings.append({"status": "ok", "label": "Prices comply with MRP regulations."})
    return verdict, savings, findings

# --- 3. THE AI BRAIN ---
def extract_financial_data(raw_text: str, category: str):
    cat = category.lower()
    
    # Define dynamic schema based on category
    schemas = {
        "rental": """"landlord_name": "str", "monthly_rent": 0.0, "security_deposit": 0.0, "cleaning_fee": 0.0""",
        "restaurant": """"restaurant_name": "str", "subtotal": 0.0, "cgst": 0.0, "sgst": 0.0, "service_charge": 0.0, "total_amount": 0.0""",
        "retail": """"store_name": "str", "items_purchased": [{"name": "str", "mrp": 0.0, "sale_price": 0.0}], "carry_bag_fee": 0.0, "total_amount": 0.0"""
    }
    schema = schemas.get(cat, """"vendor_name": "str", "subtotal": 0.0, "taxes": 0.0, "total_amount": 0.0""")

    system_prompt = f"""
    You are a FinTech Auditor. 
    1. Verify if the OCR text is actually a '{category}' document.
    2. Extract the data into the following JSON schema: {{{schema}}}
    Return ONLY the JSON.
    """
    
    response = client.chat.completions.create(
        messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": raw_text}],
        model="llama-3.1-8b-instant", 
        response_format={"type": "json_object"}
    )
    return json.loads(response.choices[0].message.content)

# --- 4. THE API ROUTES ---
@app.get("/")
def home():
    return {"status": "OmniAudit Backend is Live", "database": "Connected" if DATABASE_URL else "Missing"}

@app.post("/analyze-document/")
async def analyze_document(category: str = Form(...), file: UploadFile = File(...)):
    try:
        image_bytes = await file.read()
        raw_text = ""
        
        # Handle PDF vs Images
        if file.filename.lower().endswith('.pdf'):
            pdf = fitz.open(stream=image_bytes, filetype="pdf")
            for page in pdf:
                pix = page.get_pixmap()
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                raw_text += pytesseract.image_to_string(img) + "\n"
        else:
            image = Image.open(io.BytesIO(image_bytes))
            raw_text = pytesseract.image_to_string(image)

        if not raw_text.strip(): 
            return {"verdict": "error", "message": "OCR failed. Please provide a clearer image."}

        # AI Extraction
        ai_data = extract_financial_data(raw_text, category)
        
        # Routing to specific math engine
        cat = category.lower()
        if cat == "rental": verdict, savings, findings = evaluate_rental_rules(ai_data)
        elif cat == "restaurant": verdict, savings, findings = evaluate_restaurant_rules(ai_data)
        elif cat in ["grocery", "pharmacy", "retail"]: verdict, savings, findings = evaluate_retail_rules(ai_data)
        else: verdict, savings, findings = "FAIR", 0, [{"status": "ok", "label": "Document processed successfully."}]

        return {
            "verdict": verdict, 
            "savings": savings, 
            "findings": findings,
            "extracted_data": ai_data
        }

    except Exception as e:
        return {"verdict": "error", "message": str(e)}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
