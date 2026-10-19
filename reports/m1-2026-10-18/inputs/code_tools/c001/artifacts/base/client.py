def to_page_model(payload):
    return {"state": payload["state"]}

def load_page_model(client):
    response = client.get("/api/v1/profile")
    response.raise_for_status()
    return to_page_model(response.json())
