import os


def _is_supabase_configured():
    return bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_ROLE_KEY"))


def get_storage():
    if _is_supabase_configured():
        from app.storage import supabase_storage

        print("STORAGE MODE: supabase")
        return supabase_storage

    from app.storage import stub_storage

    print("STORAGE MODE: stub")
    return stub_storage
