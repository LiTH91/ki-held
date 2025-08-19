import json
import logging
import sys
from datetime import datetime
from typing import Any, Optional
from contextvars import ContextVar

# Context variables for request/task correlation
current_request_id: ContextVar[Optional[str]] = ContextVar('current_request_id', default=None)
current_task_id: ContextVar[Optional[str]] = ContextVar('current_task_id', default=None)

class JsonFormatter(logging.Formatter):
    """Custom JSON formatter for structured logging."""
    
    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            'timestamp': datetime.utcnow().isoformat(),
            'level': record.levelname,
            'message': record.getMessage(),
            'logger': record.name,
        }
        
        # Add request/task IDs if available
        request_id = current_request_id.get()
        task_id = current_task_id.get()
        if request_id:
            log_data['request_id'] = request_id
        if task_id:
            log_data['task_id'] = task_id
            
        # Add extra fields from record
        if hasattr(record, 'extra_fields'):
            log_data.update(record.extra_fields)
            
        # Add exception info if present
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)
            
        return json.dumps(log_data)

def setup_logging():
    """Configure JSON logging to stdout."""
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Add JSON stdout handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root_logger.addHandler(handler)

def set_request_context(request_id: str):
    """Set the current request ID in context."""
    current_request_id.set(request_id)

def set_task_context(task_id: str):
    """Set the current task ID in context."""
    current_task_id.set(task_id)

def clear_context():
    """Clear request and task context."""
    current_request_id.set(None)
    current_task_id.set(None)

def get_logger(name: str):
    """Get a logger with the given name."""
    return logging.getLogger(name)

def log_with_context(logger: logging.Logger, level: int, msg: str, extra: dict[str, Any] = None):
    """Log a message with current context and extra fields."""
    if extra is None:
        extra = {}
    record = logging.LogRecord(
        name=logger.name,
        level=level,
        pathname='',
        lineno=0,
        msg=msg,
        args=(),
        exc_info=None
    )
    record.extra_fields = extra
    logger.handle(record)

def log(message: str) -> None:
    """Shortcut to log an info-level message."""
    logging.info(message)

