import os
import openai


def test_poe_api():
    """Test the Poe API using the OpenAI-compatible Responses API."""
    api_key = os.getenv("POE_API_KEY")
    if not api_key:
        print("Error: set the POE_API_KEY environment variable.")
        return False

    print("Testing Poe API connection...")

    client = openai.OpenAI(
        api_key=api_key,
        base_url="https://api.poe.com/v1",
    )

    test_prompt = """You are creating Anki cards.

Create 2 question-answer cards from this text:

Test text: "Water boils at 100 degrees Celsius at standard atmospheric pressure."

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
