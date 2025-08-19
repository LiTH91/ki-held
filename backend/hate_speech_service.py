from typing import List, Dict
import torch
from transformers import AutoTokenizer, AutoModelForSequenceClassification
import logging
import re

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize model and tokenizer
MODEL_NAME = "Hate-speech-CNERG/dehatebert-mono-german"

try:
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModelForSequenceClassification.from_pretrained(MODEL_NAME)
    logger.info(f"Successfully loaded model and tokenizer: {MODEL_NAME}")
except Exception as e:
    logger.error(f"Error loading model or tokenizer: {e}")
    raise

# Define hate speech categories with Austrian context
HATE_CATEGORIES = [
    "hate",           # Direct hate speech
    "offensive",      # Offensive language
    "discriminatory", # Discriminatory content
    "xenophobic",     # Xenophobic content
    "antisemitic",    # Antisemitic content
    "neither"         # Non-problematic content
]

# Austrian-specific hate speech patterns
AUSTRIAN_HATE_PATTERNS = {
    "discriminatory": [
        "ausländer",
        "asylant",
        "tschusch",
        "kanake",
        "zuagroaste",
        "gscherte",
        "sandler",
        "bimbo",
        "neger",  # Highly offensive term
        "zigeuner",  # Offensive term for Roma
        "jugo",  # Derogatory term
        "piefke",  # Derogatory term for Germans
    ],
    "offensive": [
        "oasch",
        "trottel",
        "deppat",
        "gfrasst",
        "wappler",
        "vollkoffer",
        "gschissener",
        "tepf",
        "dodl",
        "beidl",
        "giftler",
        "ungustl",
    ],
    "xenophobic": [
        "ausländerpack",
        "schmarotzer",
        "sozialschmarotzer",
        "wirtschaftsflüchtling",
        "asylflut",
        "überfremdung",
        "islamisierung",
    ],
    "antisemitic": [
        "ostküste",  # Common antisemitic dog whistle
        "globalisten",
        "rothschild",
        "soros",  # Often used in antisemitic context
        "zionisten",
    ]
}

# Austrian dialect patterns and their standard German equivalents
AUSTRIAN_DIALECT_MAPPING = {
    "ned": "nicht",
    "net": "nicht",
    "nit": "nicht",
    "oida": "alter",
    "hawara": "freund",
    "leiwand": "toll",
    "servas": "servus",
    "griasdi": "grüß dich",
    "heast": "hör mal",
    "geh": "komm schon",
    "pfiat di": "behüte dich",
    "habedere": "auf wiedersehen",
    "bussi": "küsschen",
    "baba": "tschüss",
}

def normalize_austrian_text(text: str) -> str:
    """
    Convert Austrian dialect words to standard German for better model understanding.
    """
    text_lower = text.lower()
    for dialect, standard in AUSTRIAN_DIALECT_MAPPING.items():
        # Use word boundaries to avoid partial word matches
        text_lower = re.sub(rf'\b{dialect}\b', standard, text_lower)
    return text_lower

def analyze_austrian_context(text: str) -> List[str]:
    """
    Analyze text for Austrian-specific problematic content.
    Returns list of detected categories.
    """
    text_lower = text.lower()
    categories = set()
    
    # Check each category's patterns
    for category, patterns in AUSTRIAN_HATE_PATTERNS.items():
        if any(pattern in text_lower for pattern in patterns):
            categories.add(category)
            
    # Additional context analysis
    words = text_lower.split()
    
    # Check for combined words (common in Austrian hate speech)
    for word in words:
        if any(pattern in word for pattern in sum(AUSTRIAN_HATE_PATTERNS.values(), [])):
            categories.add("hate")
            
    # Check for typical sentence structures that might indicate hate speech
    if re.search(r'alle\s+\w+\s+(sind|san)', text_lower):  # "alle X sind/san" pattern
        categories.add("discriminatory")
        
    return list(categories)

async def detect_hate_speech(text: str) -> Dict:
    """
    Detect hate speech in German text with special consideration for Austrian context.
    
    Args:
        text (str): The German text to analyze
        
    Returns:
        dict: Contains the following keys:
            - is_hate (bool): True if hate speech is detected
            - confidence (float): Confidence score of the prediction
            - categories (List[str]): Relevant hate speech categories
            - explanation (str): Human-readable explanation in German
            - dialect_detected (bool): Whether Austrian dialect was detected
    """
    try:
        # Input validation
        if not text or not isinstance(text, str):
            raise ValueError("Input must be a non-empty string")
        
        # Check for Austrian dialect
        original_text = text
        dialect_detected = any(word in text.lower() for word in AUSTRIAN_DIALECT_MAPPING.keys())
        
        # Normalize text for better model understanding
        text = normalize_austrian_text(text)
            
        # Tokenize and prepare input
        inputs = tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
        
        # Get model prediction
        with torch.no_grad():
            outputs = model(**inputs)
            probabilities = torch.nn.functional.softmax(outputs.logits, dim=-1)
            
        # Get prediction scores
        scores = probabilities[0].tolist()
        predicted_class = torch.argmax(probabilities[0]).item()
        confidence = max(scores)
        
        # Initialize categories list
        categories = []
        
        # Check model prediction
        is_hate = predicted_class == 1  # Model specific: 1 for hate, 0 for non-hate
        if is_hate:
            categories.append("hate")
            
            # Get additional Austrian-specific categories
            austrian_categories = analyze_austrian_context(original_text)
            categories.extend(cat for cat in austrian_categories if cat not in categories)
        else:
            categories.append("neither")
            
        # Generate detailed explanation in German
        if is_hate:
            category_explanations = {
                "hate": "hasserfüllte Sprache",
                "offensive": "beleidigende Ausdrücke",
                "discriminatory": "diskriminierende Inhalte",
                "xenophobic": "fremdenfeindliche Äußerungen",
                "antisemitic": "antisemitische Andeutungen"
            }
            
            detected_categories = [category_explanations.get(cat, cat) for cat in categories if cat != "neither"]
            
            explanation = (
                f"Der Text wurde mit {confidence:.1%} Konfidenz als problematisch eingestuft. "
                f"Erkannte Probleme: {', '.join(detected_categories)}. "
            )
            
            if dialect_detected:
                explanation += "Der Text enthält österreichische Dialektausdrücke. "
                
            explanation += "Dieser Inhalt könnte als verletzend oder diskriminierend wahrgenommen werden."
        else:
            explanation = (
                f"Der Text wurde mit {confidence:.1%} Konfidenz als unbedenklich eingestuft. "
            )
            if dialect_detected:
                explanation += "Österreichische Dialektausdrücke wurden erkannt, aber als harmlos eingestuft. "
            else:
                explanation += "Es wurden keine problematischen Inhalte erkannt."
            
        # Return a structured dictionary
        return {
            "is_hate": is_hate,
            "confidence": float(confidence),
            "categories": categories,
            "explanation": explanation,
            "dialect_detected": dialect_detected
        }
        
    except Exception as e:
        logger.error(f"Error in hate speech detection: {e}")
        raise

def get_model_info() -> Dict:
    """
    Get information about the currently loaded model.
    
    Returns:
        dict: Contains model name and other relevant information
    """
    return {
        "model_name": MODEL_NAME,
        "categories": HATE_CATEGORIES,
        "description": "German BERT model fine-tuned for hate speech detection with Austrian context",
        "language": "German (with Austrian dialect support)",
        "dialect_support": list(AUSTRIAN_DIALECT_MAPPING.keys()),
        "pattern_categories": list(AUSTRIAN_HATE_PATTERNS.keys())
    }

