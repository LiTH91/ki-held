import unittest
from unittest.mock import patch, MagicMock
import torch
import pytest

from backend.hate_speech_service import (
    detect_hate_speech,
    get_model_info,
    normalize_austrian_text,
    analyze_austrian_context,
    AUSTRIAN_DIALECT_MAPPING,
    AUSTRIAN_HATE_PATTERNS
)

class TestHateSpeechDetection(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Create mock tensor outputs
        self.mock_logits = torch.tensor([[0.1, 0.9]])  # Example logits for hate class
        self.mock_outputs = MagicMock()
        self.mock_outputs.logits = self.mock_logits

    def test_normalize_austrian_text(self):
        # Test basic dialect normalization
        self.assertEqual(
            normalize_austrian_text("I bin ned dabei"),
            "i bin nicht dabei"
        )
        
        # Test multiple dialect words
        self.assertEqual(
            normalize_austrian_text("Servas oida, bist leiwand"),
            "servus alter, bist toll"
        )
        
        # Test word boundaries
        self.assertEqual(
            normalize_austrian_text("nederland"),  # shouldn't convert 'ned' inside word
            "nederland"
        )

    def test_analyze_austrian_context(self):
        # Test discriminatory content
        categories = analyze_austrian_context("Diese Zuagroasten kommen alle her")
        self.assertIn("discriminatory", categories)
        
        # Test offensive content
        categories = analyze_austrian_context("Du bist so a Wappler")
        self.assertIn("offensive", categories)
        
        # Test xenophobic content
        categories = analyze_austrian_context("Die Überfremdung muss gestoppt werden")
        self.assertIn("xenophobic", categories)
        
        # Test combined categories
        categories = analyze_austrian_context("Diese Ausländerpack sind alle Schmarotzer")
        self.assertTrue({"xenophobic", "discriminatory"}.issubset(set(categories)))

    @patch('backend.hate_speech_service.model')
    @patch('backend.hate_speech_service.tokenizer')
    async def test_detect_austrian_dialect_hate_speech(self, mock_tokenizer, mock_model):
        # Setup mock returns
        mock_tokenizer.return_value = {"input_ids": torch.tensor([[1, 2, 3]])}
        mock_model.return_value = self.mock_outputs

        # Test text with Austrian dialect and hate speech
        result = await detect_hate_speech("Oida, diese gscherten Zuagroasten san alle gleich")

        self.assertTrue(result["is_hate"])
        self.assertTrue(result["dialect_detected"])
        self.assertIn("discriminatory", result["categories"])
        self.assertIn("Dialektausdrücke", result["explanation"])

    @patch('backend.hate_speech_service.model')
    @patch('backend.hate_speech_service.tokenizer')
    async def test_detect_austrian_dialect_neutral(self, mock_tokenizer, mock_model):
        # Setup mock returns for non-hate class
        mock_tokenizer.return_value = {"input_ids": torch.tensor([[1, 2, 3]])}
        mock_outputs = MagicMock()
        mock_outputs.logits = torch.tensor([[0.9, 0.1]])
        mock_model.return_value = mock_outputs

        # Test neutral text with Austrian dialect
        result = await detect_hate_speech("Servas, bist echt leiwand, hawara!")

        self.assertFalse(result["is_hate"])
        self.assertTrue(result["dialect_detected"])
        self.assertEqual(result["categories"], ["neither"])
        self.assertIn("Dialektausdrücke", result["explanation"])
        self.assertIn("harmlos", result["explanation"])

    @patch('backend.hate_speech_service.model')
    @patch('backend.hate_speech_service.tokenizer')
    async def test_detect_antisemitic_dogwhistles(self, mock_tokenizer, mock_model):
        # Setup mock returns
        mock_tokenizer.return_value = {"input_ids": torch.tensor([[1, 2, 3]])}
        mock_model.return_value = self.mock_outputs

        # Test text with antisemitic dog whistles
        result = await detect_hate_speech("Die Globalisten von der Ostküste kontrollieren alles")

        self.assertTrue(result["is_hate"])
        self.assertIn("antisemitic", result["categories"])
        self.assertIn("antisemitische", result["explanation"])

    @patch('backend.hate_speech_service.model')
    @patch('backend.hate_speech_service.tokenizer')
    async def test_detect_combined_hate_patterns(self, mock_tokenizer, mock_model):
        # Setup mock returns
        mock_tokenizer.return_value = {"input_ids": torch.tensor([[1, 2, 3]])}
        mock_model.return_value = self.mock_outputs

        # Test text combining multiple hate patterns
        result = await detect_hate_speech("Diese gscherten Asylanten sind alle Sozialschmarotzer")

        self.assertTrue(result["is_hate"])
        self.assertTrue({"discriminatory", "xenophobic"}.issubset(set(result["categories"])))

    async def test_detect_hate_speech_invalid_input(self):
        # Test empty input
        with self.assertRaises(ValueError):
            await detect_hate_speech("")

        # Test non-string input
        with self.assertRaises(ValueError):
            await detect_hate_speech(123)

    def test_get_model_info(self):
        info = get_model_info()
        self.assertIn("model_name", info)
        self.assertIn("categories", info)
        self.assertIn("description", info)
        self.assertEqual(info["language"], "German (with Austrian dialect support)")
        
        # Test dialect support
        self.assertIn("dialect_support", info)
        self.assertTrue(set(AUSTRIAN_DIALECT_MAPPING.keys()).issubset(set(info["dialect_support"])))
        
        # Test pattern categories
        self.assertIn("pattern_categories", info)
        self.assertTrue(set(AUSTRIAN_HATE_PATTERNS.keys()).issubset(set(info["pattern_categories"])))

if __name__ == '__main__':
    unittest.main()