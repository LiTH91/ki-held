import asyncio
from typing import TypeVar, Callable, Awaitable
from functools import wraps
import random
from backend.logging_service import get_logger

logger = get_logger(__name__)
T = TypeVar('T')

class TransientError(Exception):
    """Error class for temporary failures that may succeed on retry."""
    pass

async def exponential_backoff(
    func: Callable[..., Awaitable[T]],
    *args,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 10.0,
    **kwargs
) -> T:
    """
    Execute an async function with exponential backoff retry logic.
    
    Args:
        func: Async function to execute
        max_attempts: Maximum number of retry attempts
        base_delay: Initial delay between retries in seconds
        max_delay: Maximum delay between retries in seconds
    """
    attempt = 1
    while True:
        try:
            return await func(*args, **kwargs)
        except TransientError as e:
            if attempt >= max_attempts:
                logger.error(f"Max retry attempts ({max_attempts}) reached", 
                           extra={'error': str(e), 'attempts': attempt})
                raise
            
            delay = min(base_delay * (2 ** (attempt - 1)) + random.uniform(0, 1), max_delay)
            logger.warning(f"Transient error, retrying in {delay:.2f}s", 
                         extra={'error': str(e), 'attempt': attempt, 'delay': delay})
            
            await asyncio.sleep(delay)
            attempt += 1
        except Exception as e:
            logger.error("Non-transient error occurred", 
                        extra={'error': str(e), 'attempt': attempt})
            raise

def with_retry(**retry_kwargs):
    """Decorator to add retry logic to an async function."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            return await exponential_backoff(func, *args, **retry_kwargs, **kwargs)
        return wrapper
    return decorator

