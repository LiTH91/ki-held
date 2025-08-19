import json
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image
from reportlab.lib.units import inch
from pyhanko.sign import signers
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.sign.fields import SigFieldSpec
import logging

logger = logging.getLogger(__name__)

def build_report(save_folder: Path, manifest_path: Path, screenshots: list[Path]) -> Path:
    """
    Generates a PDF report with screenshots and manifest information.
    Returns the path to the generated PDF.
    """
    pdf_path = save_folder / "report.pdf"
    doc = SimpleDocTemplate(str(pdf_path), pagesize=letter)
    styles = getSampleStyleSheet()
    story = []

    # Load manifest data
    with open(manifest_path) as f:
        manifest = json.load(f)

    # Title and metadata
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        spaceAfter=30
    )
    story.append(Paragraph("Evidence Report", title_style))
    story.append(Paragraph(f"Source URL: {manifest['source_url']}", styles['Normal']))
    story.append(Paragraph(f"Collected at: {manifest['collected_at']}", styles['Normal']))
    story.append(Spacer(1, 0.5 * inch))

    # Add screenshots with captions
    for i, screenshot in enumerate(screenshots, 1):
        try:
            # Add screenshot
            img = Image(str(screenshot), width=6*inch, height=8*inch, kind='proportional')
            story.append(img)
            
            # Add caption
            caption = f"Screenshot {i}"
            story.append(Paragraph(caption, styles['Normal']))
            story.append(Spacer(1, 0.5 * inch))
        except Exception as e:
            logger.error(f"Error adding screenshot {screenshot}: {e}")

    # Add manifest summary
    story.append(Paragraph("Evidence Summary", styles['Heading2']))
    story.append(Paragraph(f"Platform: {manifest['platform']}", styles['Normal']))
    story.append(Paragraph(f"Number of comments: {len(manifest['comments'])}", styles['Normal']))
    
    # Generate PDF
    doc.build(story)
    return pdf_path

def sign_pdf(pdf_path: Path, signer_key: Path | None = None, signer_cert: Path | None = None) -> Path:
    """
    Signs a PDF using pyHanko if signer credentials are provided.
    Returns the path to the signed PDF (or original if no signing performed).
    """
    if not (signer_key and signer_cert):
        logger.info("No signing credentials provided, returning unsigned PDF")
        return pdf_path

    try:
        # TODO: In production, implement secure key management
        # - Store keys in secure hardware (HSM)
        # - Use key management service (AWS KMS, Azure Key Vault)
        # - Implement proper access controls and audit logging
        
        signed_pdf_path = pdf_path.parent / f"{pdf_path.stem}_signed.pdf"
        
        # Configure signer
        signer = signers.SimpleSigner.load(
            key_file=str(signer_key),
            cert_file=str(signer_cert)
        )
        
        # Sign PDF
        with open(pdf_path, 'rb') as inf:
            w = IncrementalPdfFileWriter(inf)
            fields.append_signature_field(
                w, SigFieldSpec('Signature1', box=(50, 50, 150, 100))
            )
            
            with open(signed_pdf_path, 'wb') as outf:
                signers.sign_pdf(
                    w,
                    signers.PdfSignatureMetadata(field_name='Signature1'),
                    signer=signer,
                    output=outf
                )
        
        logger.info(f"PDF signed successfully: {signed_pdf_path}")
        return signed_pdf_path
        
    except Exception as e:
        logger.error(f"Error signing PDF: {e}")
        return pdf_path  # Return original on error

