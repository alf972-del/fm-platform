"""
services/qr.py — Generación de códigos QR para activos
=======================================================
Genera QR con URL deep-link a la app móvil.
Sube la imagen a S3/MinIO y devuelve la URL pública.
"""

import uuid
import io
import qrcode
from qrcode.image.pil import PilImage
import aioboto3

from config import settings


class QRService:
    def __init__(self):
        self.s3_session = aioboto3.Session()

    async def generate_and_upload(
        self,
        entity_type: str,
        entity_id: str,
        tenant_id: str,
    ) -> str:
        """
        Genera un QR que apunta al deep-link de la app móvil:
        fmplatform://assets/{entity_id}
        
        Sube la imagen PNG a S3 y devuelve la URL pública.
        """
        # Deep-link para la app React Native
        deep_link = f"fmplatform://{entity_type}s/{entity_id}"

        # Generar QR
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=10,
            border=4,
        )
        qr.add_data(deep_link)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")

        # Convertir a bytes
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)

        # Subir a S3
        s3_key = f"qr/{tenant_id}/{entity_type}s/{entity_id}.png"

        async with self.s3_session.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
            region_name=settings.S3_REGION,
        ) as s3:
            await s3.put_object(
                Bucket=settings.S3_BUCKET,
                Key=s3_key,
                Body=buffer.getvalue(),
                ContentType="image/png",
                ACL="public-read",
                Metadata={
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "tenant_id": tenant_id,
                },
            )

        return f"https://{settings.S3_BUCKET}.s3.{settings.S3_REGION}.amazonaws.com/{s3_key}"

    async def generate_presigned_download(self, s3_key: str) -> str:
        """URL pre-firmada para descarga del QR (15 min)."""
        async with self.s3_session.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
        ) as s3:
            url = await s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": settings.S3_BUCKET, "Key": s3_key},
                ExpiresIn=settings.S3_PRESIGNED_EXPIRY,
            )
        return url
