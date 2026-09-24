from lumira_shared import BaseServiceSettings


class ClassifierSettings(BaseServiceSettings):
    service_name: str = "classifier"
    port: int = 8003
