import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")

supabase = create_client(url, key)

# # Insert a dummy row into users table to confirm write access works
# test_user = supabase.table("users").insert({
#     "email": "test@example.com",
#     "hashed_password": "placeholder_not_real_hash"
# }).execute()

# print("Insert result:", test_user)

# # Read it back to confirm round-trip works
# result = supabase.table("users").select("*").execute()
# print("All users:", result.data)
supabase.table("users").delete().eq("email", "test@example.com").execute()