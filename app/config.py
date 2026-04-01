from pydantic import BaseModel


class Settings(BaseModel):
    shopify_webhook_secret: str = ""
    tshirtjunkies_base_url: str = "https://tshirtjunkies.co"
    host: str = "0.0.0.0"
    port: int = 8000


settings = Settings()
