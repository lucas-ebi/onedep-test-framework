import logging

_log_file = "onedep_test.log"


def set_log_file(path: str):
    """Set the log file path for all file loggers."""
    global _log_file
    _log_file = path


def get_file_logger(name: str) -> logging.Logger:
    """
    Returns a logger instance with the specified name.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    
    # File handler
    file_handler = logging.FileHandler(_log_file)
    file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(file_formatter)
    
    # Clear existing handlers and add the file handler
    logger.handlers.clear()
    logger.addHandler(file_handler)
    
    # Prevent propagation to the root logger
    logger.propagate = False
    
    return logger
