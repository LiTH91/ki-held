import os
import json
import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import shutil
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
import hmac
import base64

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
EVIDENCE_ROOT = Path("evidence")
HASH_SUFFIX = ".sha256"
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Get signature key from environment
SIGNATURE_KEY = os.getenv("EVIDENCE_SIGNATURE_KEY")
if not SIGNATURE_KEY:
    raise ValueError("EVIDENCE_SIGNATURE_KEY environment variable must be set")

class EvidenceManager:
    def __init__(self, username: str):
        """Initialize evidence manager for a specific user."""
        self.username = username
        self.user_folder = EVIDENCE_ROOT / username
        self.screenshots_folder = self.user_folder / "screenshots"
        self.metadata_folder = self.user_folder / "metadata"
        self.reports_folder = self.user_folder / "reports"
        
        # Create folder structure
        for folder in [self.user_folder, self.screenshots_folder, 
                      self.metadata_folder, self.reports_folder]:
            folder.mkdir(parents=True, exist_ok=True)

    def _calculate_file_hash(self, file_path: Path) -> str:
        """Calculate SHA256 hash of a file."""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    def _save_hash(self, file_path: Path, file_hash: str):
        """Save hash to a separate file."""
        hash_path = Path(str(file_path) + HASH_SUFFIX)
        with open(hash_path, "w") as f:
            f.write(file_hash)

    def _verify_hash(self, file_path: Path) -> bool:
        """Verify file hash against stored hash."""
        hash_path = Path(str(file_path) + HASH_SUFFIX)
        if not hash_path.exists():
            return False
        
        with open(hash_path, "r") as f:
            stored_hash = f.read().strip()
        
        current_hash = self._calculate_file_hash(file_path)
        return stored_hash == current_hash

    def _sign_data(self, data: bytes) -> str:
        """Create HMAC signature for data."""
        signature = hmac.new(
            SIGNATURE_KEY.encode(),
            data,
            hashlib.sha256
        ).digest()
        return base64.b64encode(signature).decode()

    def _verify_signature(self, data: bytes, signature: str) -> bool:
        """Verify HMAC signature of data."""
        try:
            expected_signature = self._sign_data(data)
            return hmac.compare_digest(signature, expected_signature)
        except Exception as e:
            logger.error(f"Signature verification failed: {e}")
            return False

    def save_screenshot_with_timestamp(self, comment_id: str, image_data: bytes) -> Path:
        """
        Save a screenshot with timestamp and hash.
        
        Args:
            comment_id: Unique identifier for the comment
            image_data: Binary image data
            
        Returns:
            Path to saved screenshot
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{comment_id}_{timestamp}.png"
        file_path = self.screenshots_folder / filename
        
        try:
            # Save image
            with open(file_path, "wb") as f:
                f.write(image_data)
            
            # Calculate and save hash
            file_hash = self._calculate_file_hash(file_path)
            self._save_hash(file_path, file_hash)
            
            # Create signature
            signature = self._sign_data(image_data)
            sig_path = Path(str(file_path) + ".sig")
            with open(sig_path, "w") as f:
                f.write(signature)
            
            logger.info(f"Saved screenshot: {file_path}")
            return file_path
            
        except Exception as e:
            logger.error(f"Error saving screenshot: {e}")
            raise

    def save_metadata(self, comment_data: Dict) -> Path:
        """
        Save comment metadata with hash.
        
        Args:
            comment_data: Dictionary containing comment metadata
            
        Returns:
            Path to saved metadata file
        """
        try:
            # Add timestamp to metadata
            comment_data["timestamp"] = datetime.now().isoformat()
            
            # Create filename from comment ID and timestamp
            filename = f"{comment_data.get('id', 'unknown')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            file_path = self.metadata_folder / filename
            
            # Save metadata
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(comment_data, f, ensure_ascii=False, indent=2)
            
            # Calculate and save hash
            file_hash = self._calculate_file_hash(file_path)
            self._save_hash(file_path, file_hash)
            
            # Create signature
            signature = self._sign_data(json.dumps(comment_data).encode())
            sig_path = Path(str(file_path) + ".sig")
            with open(sig_path, "w") as f:
                f.write(signature)
            
            logger.info(f"Saved metadata: {file_path}")
            return file_path
            
        except Exception as e:
            logger.error(f"Error saving metadata: {e}")
            raise

    def generate_pdf_report(self, metadata_files: List[Path], screenshot_files: List[Path]) -> Path:
        """
        Generate PDF report from metadata and screenshots.
        
        Args:
            metadata_files: List of paths to metadata files
            screenshot_files: List of paths to screenshot files
            
        Returns:
            Path to generated PDF
        """
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            report_path = self.reports_folder / f"report_{timestamp}.pdf"
            
            # Create PDF document
            doc = SimpleDocTemplate(
                str(report_path),
                pagesize=A4,
                rightMargin=72,
                leftMargin=72,
                topMargin=72,
                bottomMargin=72
            )
            
            # Prepare styles
            styles = getSampleStyleSheet()
            title_style = styles["Heading1"]
            normal_style = styles["Normal"]
            
            # Build content
            content = []
            
            # Add title
            content.append(Paragraph(f"Evidence Report - {self.username}", title_style))
            content.append(Spacer(1, 12))
            
            # Add metadata
            for meta_file in metadata_files:
                if not self._verify_hash(meta_file):
                    logger.warning(f"Hash verification failed for {meta_file}")
                    continue
                    
                with open(meta_file, "r", encoding="utf-8") as f:
                    metadata = json.load(f)
                
                content.append(Paragraph(f"Comment ID: {metadata.get('id', 'unknown')}", styles["Heading2"]))
                content.append(Paragraph(f"Timestamp: {metadata.get('timestamp', 'unknown')}", normal_style))
                content.append(Paragraph(f"Content: {metadata.get('content', 'unknown')}", normal_style))
                content.append(Spacer(1, 12))
            
            # Add screenshots
            for screenshot in screenshot_files:
                if not self._verify_hash(screenshot):
                    logger.warning(f"Hash verification failed for {screenshot}")
                    continue
                    
                img = Image(str(screenshot))
                img.drawHeight = 4*inch
                img.drawWidth = 6*inch
                content.append(img)
                content.append(Spacer(1, 12))
            
            # Add hash verification table
            hash_data = []
            hash_data.append(["File", "Hash Status"])
            for file in metadata_files + screenshot_files:
                status = "✓ Valid" if self._verify_hash(file) else "✗ Invalid"
                hash_data.append([str(file.name), status])
            
            hash_table = Table(hash_data)
            hash_table.setStyle([
                ("GRID", (0, 0), (-1, -1), 1, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
            ])
            content.append(Paragraph("Hash Verification", styles["Heading2"]))
            content.append(hash_table)
            
            # Build PDF
            doc.build(content)
            
            # Calculate and save hash for the report
            report_hash = self._calculate_file_hash(report_path)
            self._save_hash(report_path, report_hash)
            
            logger.info(f"Generated report: {report_path}")
            return report_path
            
        except Exception as e:
            logger.error(f"Error generating PDF report: {e}")
            raise

    def verify_all_files(self) -> Dict[str, bool]:
        """
        Verify hashes for all files in the evidence folder.
        
        Returns:
            Dictionary mapping file paths to verification status
        """
        results = {}
        
        # Get all files except hash and signature files
        all_files = [
            f for f in self.user_folder.rglob("*")
            if f.is_file() and not str(f).endswith((HASH_SUFFIX, ".sig"))
        ]
        
        for file_path in all_files:
            results[str(file_path)] = self._verify_hash(file_path)
            
        return results
