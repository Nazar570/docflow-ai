from io import BytesIO

from fpdf import FPDF


def build_pdf_bytes(text: str) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.multi_cell(0, 10, text)
    output = pdf.output()
    if isinstance(output, (bytes, bytearray)):
        return bytes(output)
    buffer = BytesIO()
    pdf.output(buffer)
    return buffer.getvalue()
