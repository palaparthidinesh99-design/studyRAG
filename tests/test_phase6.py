import os
import uuid
import requests

BASE_URL = "http://localhost:8000"

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
    
    # 3. Upload a text PDF (tiny_test.pdf)
    pdf_path = "tests/tiny_test.pdf"
    if os.path.exists(pdf_path):
        print(f"Uploading PDF source: {pdf_path}")
        with open(pdf_path, "rb") as f:
            files = {"file": (os.path.basename(pdf_path), f, "application/pdf")}
            res = requests.post(f"{BASE_URL}/subjects/{subject_id}/sources", files=files, headers=headers)
            
        if res.status_code == 200:
            print("PDF Upload Success!")
            print(res.json())
        else:
            print(f"PDF Upload Failed with code {res.status_code}: {res.text}")
    else:
        print(f"PDF not found at {pdf_path}")
        
    print("\n" + "="*40 + "\n")
    
    # 4. Upload an image (test_photos/indianPhilosophy.jpg)
    img_path = "test_photos/indianPhilosophy.jpg"
    if os.path.exists(img_path):
        print(f"Uploading Image source for OCR: {img_path}")
        with open(img_path, "rb") as f:
            files = {"file": (os.path.basename(img_path), f, "image/jpeg")}
            res = requests.post(f"{BASE_URL}/subjects/{subject_id}/sources", files=files, headers=headers)
            
        if res.status_code == 200:
            print("Image Upload & OCR Success!")
            print(res.json())
        else:
            print(f"Image Upload Failed with code {res.status_code}: {res.text}")
    else:
        print(f"Image not found at {img_path}")

if __name__ == "__main__":
    run_test()
