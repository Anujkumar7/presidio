from presidio_analyzer import AnalyzerEngine

def test_presidio_detects_pii():
    engine = AnalyzerEngine()
    text = "My name is John Doe. Email: john@gmail.com Phone: 9876543210"
    results = engine.analyze(text=text, language="en")
    assert len(results) > 0
    entity_types = [r.entity_type for r in results]
    assert "EMAIL_ADDRESS" in entity_types
    print("PASS:", entity_types)

test_presidio_detects_pii()
