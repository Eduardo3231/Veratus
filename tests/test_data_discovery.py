from veratus_agents.data_discovery import discovery_report, missing_business_data


def test_discovery_finds_all_catalog_products_and_sources():
    report = discovery_report()

    assert report["products_total"] == 18
    assert report["products_complete"] == 9
    assert report["products_incomplete"] == 9
    assert report["segments"] == {
        "watches_total": 9,
        "feminine_total": 9,
        "necklaces": 5,
        "bracelets": 2,
        "anklets": 2,
        "ready": 9,
        "needs_information": 9,
    }
    assert (
        report["products"][0]["source_of_each_field"]["name"] == "catalog/products.json"
    )
    assert report["products"][0]["fields"]["images"]["value"]


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
