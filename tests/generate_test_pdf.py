"""Generate test PDF fixtures for pdfstruct tests."""

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
)
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"
FIXTURES_DIR.mkdir(exist_ok=True)


def generate_structured_document():
    """Generate a multi-page PDF with headings, paragraphs, tables, and lists."""
    filepath = FIXTURES_DIR / "structured.pdf"
    doc = SimpleDocTemplate(
        str(filepath),
        pagesize=letter,
        title="Employment Agreement",
        author="Kyros Groupe",
        subject="Test Document",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "CustomTitle", parent=styles["Title"], fontSize=24, spaceAfter=20
    )
    h1_style = ParagraphStyle(
        "CustomH1", parent=styles["Heading1"], fontSize=18, spaceAfter=12
    )
    h2_style = ParagraphStyle(
        "CustomH2", parent=styles["Heading2"], fontSize=14, spaceAfter=10
    )
    body_style = ParagraphStyle(
        "CustomBody", parent=styles["BodyText"], fontSize=11, spaceAfter=8
    )
    bold_style = ParagraphStyle(
        "BoldBody", parent=body_style, fontName="Helvetica-Bold"
    )

    elements = []

    # Title
    elements.append(Paragraph("Employment Agreement", title_style))
    elements.append(Spacer(1, 12))

    # Section 1
    elements.append(Paragraph("1. Parties Involved", h1_style))
    elements.append(Paragraph(
        "This Employment Agreement (the 'Agreement') is entered into as of January 15, 2026, "
        "by and between Kyros Groupe Inc., a Delaware corporation with its principal office "
        "at 100 Innovation Drive, Suite 400, San Francisco, CA 94105 (the 'Company'), and "
        "Jane Smith, residing at 742 Evergreen Terrace, Springfield, IL 62704 (the 'Employee').",
        body_style,
    ))
    elements.append(Spacer(1, 8))

    # Section 2
    elements.append(Paragraph("2. Position and Duties", h1_style))
    elements.append(Paragraph(
        "The Employee is hired for the position of Senior Software Engineer. "
        "The Employee shall report to the VP of Engineering and perform duties "
        "consistent with this position, including but not limited to:",
        body_style,
    ))
    elements.append(Paragraph("- Designing and implementing software solutions", body_style))
    elements.append(Paragraph("- Code review and mentoring junior developers", body_style))
    elements.append(Paragraph("- Participating in architecture decisions", body_style))
    elements.append(Paragraph("- On-call rotation (one week per month)", body_style))
    elements.append(Spacer(1, 8))

    # Section 2.1
    elements.append(Paragraph("2.1 Work Schedule", h2_style))
    elements.append(Paragraph(
        "The Employee shall work a standard 40-hour work week, Monday through Friday. "
        "Remote work is permitted up to 3 days per week, subject to manager approval.",
        body_style,
    ))
    elements.append(Spacer(1, 8))

    # Section 3 with table
    elements.append(Paragraph("3. Compensation", h1_style))
    elements.append(Paragraph(
        "The Employee shall receive the following compensation package:",
        body_style,
    ))

    comp_data = [
        ["Component", "Amount", "Frequency"],
        ["Base Salary", "$185,000", "Annual"],
        ["Signing Bonus", "$25,000", "One-time"],
        ["Stock Options", "10,000 shares", "4-year vest"],
        ["Performance Bonus", "Up to 20%", "Annual"],
    ]
    comp_table = Table(comp_data, colWidths=[2 * inch, 1.5 * inch, 1.5 * inch])
    comp_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 11),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 1), (-1, -1), 10),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#ecf0f1")]),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
    ]))
    elements.append(comp_table)
    elements.append(Spacer(1, 12))

    # Section 4
    elements.append(Paragraph("4. Benefits", h1_style))
    elements.append(Paragraph(
        "The Employee is eligible for the Company's standard benefits package, including:",
        body_style,
    ))
    elements.append(Paragraph("a) Health insurance (medical, dental, vision)", body_style))
    elements.append(Paragraph("b) 401(k) with 4% company match", body_style))
    elements.append(Paragraph("c) 20 days paid time off per year", body_style))
    elements.append(Paragraph("d) 10 paid holidays", body_style))
    elements.append(Paragraph("e) Professional development budget: $3,000/year", body_style))
    elements.append(Spacer(1, 8))

    # Page break
    elements.append(PageBreak())

    # Section 5
    elements.append(Paragraph("5. Termination", h1_style))
    elements.append(Paragraph(
        "Either party may terminate this Agreement with 30 days written notice. "
        "The Company reserves the right to terminate immediately for cause, including "
        "but not limited to: violation of company policies, breach of confidentiality, "
        "or failure to perform assigned duties.",
        body_style,
    ))
    elements.append(Spacer(1, 8))

    elements.append(Paragraph("5.1 Severance", h2_style))
    elements.append(Paragraph(
        "In the event of termination without cause, the Employee shall receive "
        "3 months base salary as severance pay, plus continuation of health benefits "
        "for the same period.",
        body_style,
    ))
    elements.append(Spacer(1, 8))

    # Section 6
    elements.append(Paragraph("6. Confidentiality and Non-Compete", h1_style))
    elements.append(Paragraph(
        "The Employee agrees to maintain strict confidentiality of all proprietary "
        "information. The Employee shall not, for a period of 12 months following "
        "termination, engage in any business that directly competes with the Company "
        "within a 50-mile radius of any Company office.",
        body_style,
    ))
    elements.append(Spacer(1, 8))

    elements.append(Paragraph("WARNING: Non-compete clauses may not be enforceable in all jurisdictions.", bold_style))
    elements.append(Spacer(1, 8))

    # Section 7
    elements.append(Paragraph("7. Governing Law", h1_style))
    elements.append(Paragraph(
        "This Agreement shall be governed by and construed in accordance with the laws "
        "of the State of California, without regard to its conflict of law provisions.",
        body_style,
    ))
    elements.append(Spacer(1, 20))

    # Signatures
    elements.append(Paragraph("Signatures", h1_style))
    sig_data = [
        ["Company Representative", "Employee"],
        ["_______________________", "_______________________"],
        ["Name: John CEO", "Name: Jane Smith"],
        ["Title: CEO", "Date: January 15, 2026"],
        ["Date: January 15, 2026", ""],
    ]
    sig_table = Table(sig_data, colWidths=[3 * inch, 3 * inch])
    sig_table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(sig_table)

    doc.build(elements)
    print(f"Generated: {filepath}")
    return filepath


def generate_simple_document():
    """Generate a simple single-page PDF with no structure."""
    filepath = FIXTURES_DIR / "simple.pdf"
    doc = SimpleDocTemplate(str(filepath), pagesize=letter)
    styles = getSampleStyleSheet()

    elements = [
        Paragraph(
            "This is a simple document with no headings or structure. "
            "It contains just a few paragraphs of plain text for testing purposes.",
            styles["BodyText"],
        ),
        Spacer(1, 12),
        Paragraph(
            "The second paragraph discusses the weather. It was a sunny day "
            "in San Francisco, with temperatures reaching 72 degrees Fahrenheit.",
            styles["BodyText"],
        ),
        Spacer(1, 12),
        Paragraph(
            "The third and final paragraph concludes this simple test document. "
            "Nothing more to see here.",
            styles["BodyText"],
        ),
    ]

    doc.build(elements)
    print(f"Generated: {filepath}")
    return filepath


def generate_image_document():
    """Generate a PDF with embedded images for image extraction tests."""
    from reportlab.platypus import Image as RLImage
    from PIL import Image as PILImage
    import tempfile, os

    filepath = FIXTURES_DIR / "with_images.pdf"

    # Create test images as temp files
    tmp_dir = tempfile.mkdtemp()
    img1_path = os.path.join(tmp_dir, "test_blue.png")
    img2_path = os.path.join(tmp_dir, "test_green.jpg")

    pil_img1 = PILImage.new("RGB", (200, 150), color=(0, 0, 255))
    pil_img1.save(img1_path, "PNG")

    pil_img2 = PILImage.new("RGB", (300, 200), color=(0, 128, 0))
    pil_img2.save(img2_path, "JPEG")

    doc = SimpleDocTemplate(
        str(filepath),
        pagesize=letter,
        title="Document with Images",
    )
    styles = getSampleStyleSheet()
    body_style = styles["BodyText"]
    title_style = styles["Title"]

    elements = [
        Paragraph("Document with Images", title_style),
        Spacer(1, 12),
        Paragraph(
            "This document contains embedded images for testing the image "
            "extraction functionality of pdfstruct.",
            body_style,
        ),
        Spacer(1, 12),
        RLImage(img1_path, width=200, height=150),
        Paragraph("Figure 1: Blue test image", body_style),
        Spacer(1, 12),
        Paragraph(
            "The above image is a simple blue rectangle. Below is a green rectangle.",
            body_style,
        ),
        Spacer(1, 12),
        RLImage(img2_path, width=300, height=200),
        Paragraph("Figure 2: Green test image", body_style),
        Spacer(1, 12),
        Paragraph("End of document.", body_style),
    ]
    doc.build(elements)

    # Clean up temp images
    os.unlink(img1_path)
    os.unlink(img2_path)
    os.rmdir(tmp_dir)

    print(f"Generated: {filepath}")
    return filepath


if __name__ == "__main__":
    generate_structured_document()
    generate_simple_document()
    generate_image_document()
    print("All test fixtures generated.")
