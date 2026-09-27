from veratus_agents.data_discovery import discovery_report, missing_business_data


def test_discovery_finds_all_catalog_products_and_sources():
    report = discovery_report()

    # Watch photos showed third-party marks and were quarantined (2026-09-27):
    # every watch waits for a real photo and is not ready for any channel.
    watches = [item for item in report["products"] if item["collection"] == "watches"]
    assert report["products_total"] == 18
    assert report["products_complete"] == 0
    assert report["products_incomplete"] == 18
    assert report["segments"] == {
        "watches_total": 9,
        "feminine_total": 9,
        "necklaces": 5,
        "bracelets": 2,
        "anklets": 2,
        "ready": 0,
        "needs_information": 18,
    }
    assert (
        report["products"][0]["source_of_each_field"]["name"] == "catalog/products.json"
    )
    assert all(item["missing_fields"] == ["images"] for item in watches)
    assert all(not item["fields"]["images"]["value"] for item in watches)


def test_missing_report_is_channel_specific_and_does_not_invent_values():
    report = discovery_report()
    missing = missing_business_data(report["products"])

    assert any(
        item["channel"] == "shopee" and item["field"] == "category_mapping"
        for item in missing
    )
    assert all(item["field"] != "material" or item["reason"] for item in missing)
    assert not any(
        item["field"] == "price" and item["classification"] == "REAL_CONFIRMED"
        for item in missing
    )
