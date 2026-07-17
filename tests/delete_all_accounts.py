import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.config import supabase

def delete_all_accounts():
    # Cascade delete: queries -> sources -> subject_books -> subjects -> users
    try:
        queries = supabase.table("queries").delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
        print(f"Deleted {len(queries.data)} queries")
    except Exception as e:
        print(f"Queries delete: {e}")

    try:
        sources = supabase.table("sources").delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
        print(f"Deleted {len(sources.data)} sources")
    except Exception as e:
        print(f"Sources delete: {e}")

    try:
        sb = supabase.table("subject_books").delete().neq("subject_id", "00000000-0000-0000-0000-000000000000").execute()
        print(f"Deleted {len(sb.data)} subject_books")
    except Exception as e:
        print(f"subject_books delete: {e}")

    try:
        subjects = supabase.table("subjects").delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
        print(f"Deleted {len(subjects.data)} subjects")
    except Exception as e:
        print(f"Subjects delete: {e}")

    try:
        users = supabase.table("users").delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
        print(f"Deleted {len(users.data)} users")
    except Exception as e:
        print(f"Users delete: {e}")

    print("\nAll accounts deleted successfully.")

if __name__ == "__main__":
    confirm = input("This will delete ALL user accounts and data. Type YES to confirm: ")
    if confirm.strip() == "YES":
        delete_all_accounts()
    else:
        print("Aborted.")
