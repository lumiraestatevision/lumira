from lumira_shared import BaseServiceSettings


class BackendSettings(BaseServiceSettings):
    service_name: str = "backend"
    port: int = 8000

    database_url: str = "postgresql+asyncpg://lumira:lumira-dev-only@localhost:5432/lumira"
    # false: Projekt ist nach model.generated fertig (Unreal-Stub läuft nicht)
    pipeline_vr_enabled: bool = False
    max_upload_mb: int = 200
    cors_origins: str = "http://localhost:3000"  # kommagetrennt

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024
