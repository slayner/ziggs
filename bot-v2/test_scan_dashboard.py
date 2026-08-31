from cogs.scan_dashboard import _build_embed


def test_embed_prioriza_status_dos_ponteiros():
    embed = _build_embed({
        "feed_pointers": {
            "americas/battles": {"active": True, "phase": "locating", "next_offset": 357},
            "americas/kills": {"blocked": True},
            "europe/battles": {"resolution": "exact_id"},
        },
    })
    fields = {field.name: field.value for field in embed.fields}
    assert "🧭 Ponteiros do inbox" in fields
    assert "🌎 Btl 🔎 procurando · off `357` · Kls 🔴 erro" in fields["🧭 Ponteiros do inbox"]
    assert "🌍 Btl ✅ encontrado" in fields["🧭 Ponteiros do inbox"]
    assert "🆔 Última batalha por tier" not in fields
    assert "📥 Última descoberta no inbox" not in fields


if __name__ == "__main__":
    test_embed_prioriza_status_dos_ponteiros()
    print("ok")
