import os
import uuid
import requests

BASE_URL = "https://studyrag-3s4g.onrender.com"

def run_test():
    # 1. Register a fresh test user
    email = f"student_{uuid.uuid4().hex[:6]}@example.com"
    password = "testpassword123"
    
    print(f"Registering user: {email}")
    res = requests.post(f"{BASE_URL}/register", json={"email": email, "password": password})
    res.raise_for_status()
    token = res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    
    # 2. Create a test subject
    subject_name = "Philosophy 101"
    print(f"Creating subject: {subject_name}")
    res = requests.post(f"{BASE_URL}/subjects", json={"name": subject_name}, headers=headers)
    res.raise_for_status()
    subject = res.json()
    subject_id = subject["id"]
    print(f"Created subject with ID: {subject_id}\n")
    
    # 3. Check for global books and link the first one if it exists
    print("Listing global books...")
    res = requests.get(f"{BASE_URL}/global-books", headers=headers)
    res.raise_for_status()
    books = res.json()
    if books:
        book_id = books[0]["id"]
        book_title = books[0]["title"]
        print(f"Linking global book '{book_title}' (ID: {book_id}) to subject...")
        res = requests.post(f"{BASE_URL}/subjects/{subject_id}/books/{book_id}", headers=headers)
        if res.status_code == 200:
            print("Book linked successfully!")
        else:
            print(f"Failed to link book: {res.text}")
    else:
        print("No global books found in database to link.")
        
    print("\n" + "="*40 + "\n")
    
    # 4. Upload a personal note PDF to subject
    pdf_path = "tests/tiny_test.pdf"
    if os.path.exists(pdf_path):
        print(f"Uploading PDF source: {pdf_path}")
        with open(pdf_path, "rb") as f:
            files = {"file": (os.path.basename(pdf_path), f, "application/pdf")}
            res = requests.post(f"{BASE_URL}/subjects/{subject_id}/sources", files=files, headers=headers)
        res.raise_for_status()
        print("PDF upload successful!")
    else:
        print(f"PDF not found at {pdf_path}")
        
    print("\n" + "="*40 + "\n")
    
    # 5. Test Text Query
    query_text = "What is the primary method of philosophy or how do philosophers search for truth?"
    print(f"Testing TEXT query: '{query_text}'")
    res = requests.post(f"{BASE_URL}/subjects/{subject_id}/query/text", json={"query": query_text}, headers=headers)
    if res.status_code == 200:
        print("TEXT Query Success!")
        resp = res.json()
        print("Answer:", resp.get("answer"))
        print("Sources Used:", resp.get("sources"))
    else:
        print(f"TEXT Query Failed: {res.text}")
        
    print("\n" + "="*40 + "\n")
    
    # 6. Test Photo Query
    img_path = "test_photos/indianPhilosophy.jpg"
    if os.path.exists(img_path):
        print(f"Testing PHOTO query with image: {img_path}")
        with open(img_path, "rb") as f:
            files = {"file": (os.path.basename(img_path), f, "image/jpeg")}
            res = requests.post(f"{BASE_URL}/subjects/{subject_id}/query/photo", files=files, headers=headers)
            
        if res.status_code == 200:
            print("PHOTO Query Success!")
            resp = res.json()
            print("Extracted Text:", resp.get("extracted_text"))
            print("Answer:", resp.get("answer"))
            print("Sources Used:", resp.get("sources"))
        else:
            print(f"PHOTO Query Failed: {res.text}")
    else:
        print(f"Image not found at {img_path}")
        
    print("\n" + "="*40 + "\n")
    
    # 7. Test History GET endpoint
    print("Fetching query history...")
    res = requests.get(f"{BASE_URL}/subjects/{subject_id}/history", headers=headers)
    if res.status_code == 200:
        history = res.json()
        print(f"History count: {len(history)} items retrieved.")
        for idx, item in enumerate(history):
            print(f"[{idx+1}] Type: {item['input_type']}, Extracted Text: {item['extracted_text'][:60]}...")
            print(f"    Answer snippet: {item['generated_answer'][:100]}...")
    else:
        print(f"Failed to fetch history: {res.text}")

if __name__ == "__main__":
    run_test()
