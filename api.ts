export type AuditStatus = 'ok' | 'warn' | 'error';
export type AuditVerdict = 'fair' | 'overcharged' | 'review';

export interface AuditFinding {
  status: AuditStatus;
  label: string;
  delta?: string;
}

export interface AuditResponse {
  parsed: {
    vendor: string;
    date: string;
    subtotal: string;
    tax: string;
    total: string;
  };
  verdict: AuditVerdict;
  savings: number;
  findings: AuditFinding[];
  recommendation: string;
}

// Your NEW, completely unblocked Railway link
const API_URL = "https://reliable-success-production-e9c9.up.railway.app/analyze-document/";

export const submitAudit = async (category: string, fileUri: string): Promise<AuditResponse> => {
  const formData = new FormData() as any; 
  
  const fileExt = fileUri.split('.').pop() || 'jpg';
  const mimeType = fileExt === 'pdf' ? 'application/pdf' : `image/${fileExt}`;

  // --- THE WEB FIX ---
  // Converts the local file into a format the web browser can successfully upload
  try {
    const fileResponse = await fetch(fileUri);
    const blob = await fileResponse.blob();
    formData.append("file", blob, `upload.${fileExt}`);
  } catch (e) {
    console.error("Failed to convert file to Blob:", e);
    throw new Error("File conversion failed");
  }

  formData.append("category", category.toLowerCase());

  try {
    // Send the data. We explicitly let the browser handle the Content-Type headers.
    const response = await fetch(API_URL, {
      method: "POST",
      body: formData,
    });

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    const rawData = await response.json();

    // --- THE UI SAFETY MAPPING ---
    // Protects the app from crashing if the AI forgets to send a specific piece of data
    const extracted = rawData.extracted_data || {};

    return {
      parsed: {
        vendor: extracted.vendor_name || extracted.restaurant_name || extracted.provider_name || extracted.store_name || 'Unknown Vendor',
        date: extracted.date || 'Unknown Date',
        subtotal: extracted.subtotal?.toString() || '-',
        tax: (extracted.cgst ? (extracted.cgst + extracted.sgst) : extracted.taxes)?.toString() || '-',
        total: extracted.total_amount?.toString() || '-',
      },
      verdict: (rawData.verdict?.toLowerCase() as AuditVerdict) || 'review',
      savings: rawData.savings || 0,
      findings: rawData.findings || [],
      recommendation: rawData.verdict === "FAIR" ? "Looks good! No action needed." : "Review the warnings in the findings below."
    };

  } catch (error) {
    console.error("Upload failed:", error);
    // Safe fallback UI state if the network drops
    return {
      parsed: { vendor: 'Error', date: 'Unknown Date', subtotal: '-', tax: '-', total: '-' },
      verdict: 'review',
      savings: 0,
      findings: [
        { status: 'error', label: 'Failed to connect to the auditing server.' }
      ],
      recommendation: 'Please check your internet connection and try uploading again.'
    };
  }
};