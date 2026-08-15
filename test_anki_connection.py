import requests
import json

ANKI_URL = "http://localhost:8765"
DECK_NAME = "Baugeschichte"

def test_anki_connection():
    """Test if Anki is running and AnkiConnect is available"""
    try:
        response = requests.post(ANKI_URL, json={
            "action": "version",
            "version": 6
        })
        
        if response.status_code == 200:
            result = response.json()
            print(f"[OK] AnkiConnect is running (version: {result.get('result', 'unknown')})")
            return True
        else:
            print(f"[ERROR] HTTP error: {response.status_code}")
            return False
            
    except requests.exceptions.ConnectionError:
        print("[ERROR] Could not connect to Anki. Make sure:")
        print("  1. Anki desktop application is running")
        print("  2. AnkiConnect addon is installed")
        print("  3. AnkiConnect is listening on localhost:8765")
        return False
    except Exception as e:
        print(f"[ERROR] Error: {e}")
        return False

def test_deck_creation():
    """Test creating the Baugeschichte deck"""
    try:
        response = requests.post(ANKI_URL, json={
            "action": "createDeck",
            "version": 6,
            "params": {"deck": DECK_NAME}
        })
        
        if response.status_code == 200:
            result = response.json()
            if result.get("error"):
                print(f"[OK] Deck '{DECK_NAME}' already exists")
            else:
                print(f"[OK] Created deck '{DECK_NAME}'")
            return True
        else:
            print(f"[ERROR] Could not create deck: HTTP {response.status_code}")
            return False
            
    except Exception as e:
        print(f"[ERROR] Error creating deck: {e}")
        return False

def test_note_model():
    """Check if Basic note model exists"""
    try:
        response = requests.post(ANKI_URL, json={
            "action": "modelNames",
            "version": 6
        })
        
        if response.status_code == 200:
            result = response.json()
            models = result.get("result", [])
            if "Basic" in models:
                print("[OK] 'Basic' note model is available")
                return True
            else:
                print(f"[ERROR] 'Basic' note model not found. Available models: {models}")
                return False
        else:
            print(f"[ERROR] Could not check note models: HTTP {response.status_code}")
            return False
            
    except Exception as e:
        print(f"[ERROR] Error checking note models: {e}")
        return False

def main():
    print("Testing Anki integration...")
    print("=" * 40)
    
    connection_ok = test_anki_connection()
    if not connection_ok:
        return
    
    deck_ok = test_deck_creation()
    model_ok = test_note_model()
    
    print("=" * 40)
    if connection_ok and deck_ok and model_ok:
        print("[SUCCESS] All tests passed! Ready to run the enhanced script.")
    else:
        print("[FAILED] Some tests failed. Please fix the issues above.")

if __name__ == "__main__":
    main()
