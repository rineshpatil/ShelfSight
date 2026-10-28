"""Micro metrics for saved, reviewed extractor predictions; unknown is not negative."""
def binary(expected, predicted):
    tp, fp, fn = len(expected & predicted), len(predicted - expected), len(expected - predicted)
    return {"tp": tp, "fp": fp, "fn": fn,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None}


def evaluate(cases):
    truth = {k: set() for k in ("mentions", "recommendations", "citations")}
    guesses = {k: set() for k in truth}
    rank_total = rank_ok = 0
    errors = []
    recommendation_unknown = 0
    for case in cases:
        cid = case["id"]
        expected = {m["brand_id"]: m for m in case["expected"]}
        got = {m["brand_id"] or m.get("brand_raw"): m for m in case["predicted"] if m.get("source") != "none"}
        for name, rows in (("expected", expected), ("predicted", got)):
            dest = truth if name == "expected" else guesses
            dest["mentions"].update((cid, b) for b in rows)
            dest["recommendations"].update((cid, b) for b, m in rows.items() if m.get("is_recommended") is True)
        for key in truth:
            if key == "citations":
                truth[key].update((cid, u) for u in case["expected_citations"])
                guesses[key].update((cid, u) for u in case["predicted_citations"])
        for brand, exp in expected.items():
            if "is_recommended" not in exp:
                raise ValueError("recommendation labels must be explicit")
            if brand in got and got[brand].get("is_recommended") is None:
                recommendation_unknown += 1
                errors.append({"case": cid, "kind": "recommendation_unknown", "brand": brand})
            rank_total += 1  # includes missed brands and expected nulls
            match = brand in got and got[brand].get("rank") == exp["rank"]
            rank_ok += match
            if not match:
                errors.append({"case": cid, "kind": "rank", "brand": brand,
                               "expected": exp["rank"], "predicted": got.get(brand, {}).get("rank", "missing")})
    scores = {}
    for key in truth:
        scores[key] = binary(truth[key], guesses[key])
        for kind, values in (("false_positive", guesses[key] - truth[key]), ("false_negative", truth[key] - guesses[key])):
            errors.extend({"case": cid, "kind": key + "_" + kind, "value": val} for cid, val in sorted(values))
    return {"metrics": scores, "recommendation_unknown": recommendation_unknown, "rank": {"correct": rank_ok, "total": rank_total,
            "accuracy": rank_ok / rank_total if rank_total else None}, "errors": errors}
