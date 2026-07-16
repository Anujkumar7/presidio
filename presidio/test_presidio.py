from presidio_analyzer import AnalyzerEngine

engine = AnalyzerEngine()

text = """
My name is John Doe.
Email: john@gmail.com
Phone: 9876543210
"""

results = engine.analyze(text=text, language="en")

for result in results:
    print(result)