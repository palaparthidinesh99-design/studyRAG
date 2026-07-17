import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Retrieve SMTP settings from environment
SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "").strip()
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "").strip()
SMTP_SENDER = os.environ.get("SMTP_SENDER", "noreply@studyrag.com").strip()

def send_verification_email(recipient_email: str, code: str) -> bool:
    """Send verification email containing a 6-digit verification code to the recipient."""
    print("=" * 60)
    print(f"EMAIL VERIFICATION CODE FOR {recipient_email}: {code}")
    print("=" * 60)

    # Check if SMTP settings are fully configured
    if not SMTP_HOST or not SMTP_USERNAME or not SMTP_PASSWORD:
        print("Warning: SMTP environment variables (SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD) are not fully configured.")
        print("Email sending skipped. Code printed to console logs above.")
        return False

    try:
        subject = "Verify Your StudyRAG Email Address"
        
        # HTML template matching the StudyRAG design aesthetics
        html = f"""
        <html>
        <body style="font-family: 'Inter', -apple-system, sans-serif; background-color: #0e0e11; color: #f3f4f6; padding: 40px; margin: 0;">
            <div style="max-width: 500px; margin: 0 auto; background-color: #0a0a0c; border: 1px solid rgba(255, 255, 255, 0.08); padding: 40px 30px; border-radius: 16px; box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);">
                <div style="text-align: center; margin-bottom: 24px;">
                    <span style="font-family: 'Outfit', sans-serif; font-size: 2.2rem; font-weight: 800; color: #ffffff; letter-spacing: -0.5px;">
                        Study<span style="color: #f05a28;">RAG</span>
                    </span>
                </div>
                <h2 style="font-family: 'Outfit', sans-serif; font-weight: 750; color: #ffffff; font-size: 1.4rem; margin-bottom: 12px; text-align: center;">Verify Your Email</h2>
                <p style="color: #9ca3af; font-size: 0.95rem; line-height: 1.6; margin-bottom: 24px; text-align: center;">
                    Thank you for signing up for StudyRAG. Please use the verification code below to complete your registration:
                </p>
                <div style="background-color: #060608; border-radius: 10px; padding: 18px 24px; margin-bottom: 24px; text-align: center; border: 1px solid rgba(255, 255, 255, 0.05);">
                    <span style="font-family: monospace; font-size: 2.2rem; font-weight: 700; letter-spacing: 8px; color: #f05a28; padding-left: 8px;">
                        {code}
                    </span>
                </div>
                <p style="color: #9ca3af; font-size: 0.85rem; line-height: 1.6; text-align: center; margin-top: 32px; border-top: 1px solid rgba(255, 255, 255, 0.08); padding-top: 16px;">
                    If you did not request this email, you can safely ignore it.
                </p>
            </div>
        </body>
        </html>
        """
        
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = SMTP_SENDER
        msg["To"] = recipient_email
        
        text_part = MIMEText(f"Your StudyRAG verification code is: {code}", "plain")
        html_part = MIMEText(html, "html")
        
        msg.attach(text_part)
        msg.attach(html_part)
        
        # Connect to SMTP server
        # Support both standard SMTP (587 TLS) and SMTP SSL (465)
        if SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15)
        else:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15)
            server.ehlo()
            server.starttls()
            server.ehlo()
            
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.sendmail(SMTP_SENDER, recipient_email, msg.as_string())
        server.quit()
        print(f"Verification email successfully sent to {recipient_email}")
        return True
    except Exception as e:
        print(f"Error: Failed to send email via SMTP to {recipient_email}: {e}")
        return False
