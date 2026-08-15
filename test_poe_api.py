import os
import openai

# Prefer environment variable; never commit real keys.
POE_API_KEY = os.getenv("POE_API_KEY", "")


def test_poe_api():
    """Test the Poe API using the OpenAI-compatible Responses API."""
    if not POE_API_KEY:
        print("[ERROR] Set POE_API_KEY environment variable first.")
        return False

    print("Testing Poe API connection...")

    client = openai.OpenAI(
        api_key=POE_API_KEY,
        base_url="https://api.poe.com/v1",
    )

    test_prompt = """You are creating Anki cards for Baugeschichte exams.

Create 2 question-answer cards from this text:

Test text: "Die Romanik ist ein Baustil des Mittelalters (etwa 1000-1200). Charakteristisch sind dicke Mauern, kleine Fenster und Rundbögen."

Format strictly as:

Q: ...
A: ..."""

    try:
        print("Sending test request to Poe API...")
        response = client.responses.create(
            model="gpt-5.2-instant",
            input=test_prompt,
            max_output_tokens=1000,
        )

        text_response = response.output_text or ""
        print("[SUCCESS] Poe API is working!")
        print("Sample response:")
        print("-" * 40)
        print(text_response)
        print("-" * 40)

        if "Q:" in text_response and "A:" in text_response:
            print("[OK] Response format is correct")
            return True

        print("[WARNING] Response format may be incorrect")
        return False

    except Exception as e:
        print(f"[ERROR] Poe API request failed: {e}")
        return False


if __name__ == "__main__":
    success = test_poe_api()
    if success:
        print("\n[READY] Poe API is working correctly!")
    else:
        print("\n[FAILED] Poe API test failed. Check the issues above.")
