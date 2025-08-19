import unittest
import tempfile
import shutil
from pathlib import Path
import json
import os
from ..evidence import EvidenceManager

class TestEvidenceManager(unittest.TestCase):
    def setUp(self):
        # Create temporary directory for tests
        self.temp_dir = tempfile.mkdtemp()
        self.original_evidence_root = Path("evidence")
        
        # Set up test environment variable if not exists
        if not os.getenv("EVIDENCE_SIGNATURE_KEY"):
            os.environ["EVIDENCE_SIGNATURE_KEY"] = "test-signature-key-123"
        
        # Patch EVIDENCE_ROOT to use temp directory
        import backend.evidence
        backend.evidence.EVIDENCE_ROOT = Path(self.temp_dir)
        
        # Create test instance
        self.manager = EvidenceManager("test_user")
        
        # Create a small valid PNG image for testing
        from PIL import Image
        import io
        
        # Create a 1x1 black pixel PNG
        img = Image.new('RGB', (1, 1), color='black')
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='PNG')
        self.test_image = img_byte_arr.getvalue()
        
        # Test metadata
        self.test_metadata = {
            "id": "test_comment_123",
            "content": "Test comment content",
            "url": "https://example.com/test"
        }

    def tearDown(self):
        # Clean up temporary directory
        shutil.rmtree(self.temp_dir)

    def test_folder_structure(self):
        """Test that folder structure is created correctly."""
        self.assertTrue(self.manager.user_folder.exists())
        self.assertTrue(self.manager.screenshots_folder.exists())
        self.assertTrue(self.manager.metadata_folder.exists())
        self.assertTrue(self.manager.reports_folder.exists())

    def test_save_screenshot(self):
        """Test screenshot saving and hash verification."""
        # Save screenshot
        screenshot_path = self.manager.save_screenshot_with_timestamp(
            "test_comment_123",
            self.test_image
        )
        
        # Verify file exists
        self.assertTrue(screenshot_path.exists())
        
        # Verify hash file exists and is valid
        hash_path = Path(str(screenshot_path) + ".sha256")
        self.assertTrue(hash_path.exists())
        
        # Verify signature file exists
        sig_path = Path(str(screenshot_path) + ".sig")
        self.assertTrue(sig_path.exists())
        
        # Verify hash is correct
        self.assertTrue(self.manager._verify_hash(screenshot_path))

    def test_save_metadata(self):
        """Test metadata saving and hash verification."""
        # Save metadata
        metadata_path = self.manager.save_metadata(self.test_metadata)
        
        # Verify file exists
        self.assertTrue(metadata_path.exists())
        
        # Verify hash file exists and is valid
        hash_path = Path(str(metadata_path) + ".sha256")
        self.assertTrue(hash_path.exists())
        
        # Verify signature file exists
        sig_path = Path(str(metadata_path) + ".sig")
        self.assertTrue(sig_path.exists())
        
        # Verify hash is correct
        self.assertTrue(self.manager._verify_hash(metadata_path))
        
        # Verify content
        with open(metadata_path, "r", encoding="utf-8") as f:
            saved_metadata = json.load(f)
        self.assertEqual(saved_metadata["id"], self.test_metadata["id"])
        self.assertIn("timestamp", saved_metadata)

    def test_hash_verification(self):
        """Test hash verification with tampered files."""
        # Save metadata
        metadata_path = self.manager.save_metadata(self.test_metadata)
        
        # Verify initial hash
        self.assertTrue(self.manager._verify_hash(metadata_path))
        
        # Tamper with file
        with open(metadata_path, "w") as f:
            f.write("tampered data")
        
        # Verify hash fails after tampering
        self.assertFalse(self.manager._verify_hash(metadata_path))

    def test_generate_pdf_report(self):
        """Test PDF report generation with hash verification."""
        # Save test data
        metadata_path = self.manager.save_metadata(self.test_metadata)
        screenshot_path = self.manager.save_screenshot_with_timestamp(
            "test_comment_123",
            self.test_image
        )
        
        # Generate report
        report_path = self.manager.generate_pdf_report(
            [metadata_path],
            [screenshot_path]
        )
        
        # Verify report exists
        self.assertTrue(report_path.exists())
        
        # Verify report hash exists and is valid
        hash_path = Path(str(report_path) + ".sha256")
        self.assertTrue(hash_path.exists())
        self.assertTrue(self.manager._verify_hash(report_path))

    def test_verify_all_files(self):
        """Test verification of all files in evidence folder."""
        # Save test data
        metadata_path = self.manager.save_metadata(self.test_metadata)
        screenshot_path = self.manager.save_screenshot_with_timestamp(
            "test_comment_123",
            self.test_image
        )
        
        # Verify all files
        results = self.manager.verify_all_files()
        
        # Check results
        self.assertTrue(results[str(metadata_path)])
        self.assertTrue(results[str(screenshot_path)])
        
        # Tamper with a file
        with open(metadata_path, "w") as f:
            f.write("tampered data")
        
        # Verify tampering is detected
        results = self.manager.verify_all_files()
        self.assertFalse(results[str(metadata_path)])
        self.assertTrue(results[str(screenshot_path)])

if __name__ == "__main__":
    unittest.main()
