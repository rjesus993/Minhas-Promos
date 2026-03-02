# logging_config.py
import logging
from logging.handlers import RotatingFileHandler

def setup_logging():
    # Configuração do logger
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # Formato do log
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Handler para arquivo
    file_handler = RotatingFileHandler(
        'bot.log', maxBytes=10*1024*1024, backupCount=5
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # Handler para console
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    return logger