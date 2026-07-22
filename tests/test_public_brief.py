from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from publish_public_brief import publish  # noqa: E402
from validate_public_brief import (  # noqa: E402
    PublicBriefError,
    load_json,
    validate_brief,
    validate_index,
)


FIXTURE = ROOT / "tests" / "fixtures" / "public-brief.json"


class PublicBriefTests(unittest.TestCase):
    def setUp(self) -> None:
        self.brief = load_json(FIXTURE)

    def test_valid_fixture(self) -> None:
        self.assertEqual(validate_brief(self.brief)["brief_id"], "example-brief")

    def test_extra_private_field_is_rejected(self) -> None:
        self.brief["account"] = "private"
        with self.assertRaisesRegex(PublicBriefError, "forbidden fields"):
            validate_brief(self.brief)

    def test_mismatched_gap_is_rejected(self) -> None:
        self.brief["actions"][0]["price_gap"] = 0.14
        with self.assertRaisesRegex(PublicBriefError, "must equal"):
            validate_brief(self.brief)

    def test_public_title_is_fixed(self) -> None:
        self.brief["title"] = "其他标题"
        with self.assertRaisesRegex(PublicBriefError, "预测市场研究信号"):
            validate_brief(self.brief)

    def test_profile_link_is_rejected(self) -> None:
        self.brief["actions"][0]["polymarket_url"] = "https://polymarket.com/@example"
        with self.assertRaisesRegex(PublicBriefError, "event URL"):
            validate_brief(self.brief)

    def test_sensitive_values_are_rejected(self) -> None:
        self.brief["actions"][0]["reason"] = "Contact person@example.com"
        with self.assertRaisesRegex(PublicBriefError, "email address"):
            validate_brief(self.brief)

    def test_partition_weights_must_sum_to_one(self) -> None:
        root = self.brief["actions"][0]["probability_tree"]["root"]
        root["branches"][1]["weight"] = 0.3
        with self.assertRaisesRegex(PublicBriefError, "weights must sum to 1"):
            validate_brief(self.brief)

    def test_unknown_evidence_reference_is_rejected(self) -> None:
        root = self.brief["actions"][0]["probability_tree"]["root"]
        root["branches"][0]["evidence_refs"] = ["missing-evidence"]
        with self.assertRaisesRegex(PublicBriefError, "unknown evidence"):
            validate_brief(self.brief)

    def test_unreferenced_evidence_is_rejected(self) -> None:
        extra = copy.deepcopy(self.brief["actions"][0]["evidence"][0])
        extra["id"] = "unused-evidence"
        self.brief["actions"][0]["evidence"].append(extra)
        with self.assertRaisesRegex(PublicBriefError, "does not reference evidence"):
            validate_brief(self.brief)

    def test_non_string_evidence_reference_has_protocol_error(self) -> None:
        root = self.brief["actions"][0]["probability_tree"]["root"]
        root["branches"][0]["evidence_refs"] = [{"not": "hashable"}]
        with self.assertRaisesRegex(PublicBriefError, "must be a non-empty string"):
            validate_brief(self.brief)

    def test_root_probability_must_equal_fair_price(self) -> None:
        action = self.brief["actions"][0]
        action["fair_price"] = 0.68
        action["price_gap"] = 0.16
        with self.assertRaisesRegex(PublicBriefError, "must equal fair_price"):
            validate_brief(self.brief)

    def test_bayes_posterior_is_recomputed(self) -> None:
        action = self.brief["actions"][0]
        action["fair_price"] = 0.5
        action["price_gap"] = -0.02
        action["evidence"] = [action["evidence"][0]]
        action["probability_tree"] = {
            "root": {
                "id": "root-update",
                "type": "bayes_update",
                "label": "证据更新后的目标概率",
                "description": "从先验开始应用一条证据。",
                "probability": 0.5,
                "evidence_refs": [],
                "prior": {
                    "id": "prior",
                    "type": "estimate",
                    "label": "先验",
                    "description": "未加入新证据前的概率。",
                    "probability": 0.4,
                    "evidence_refs": [],
                },
                "updates": [
                    {
                        "id": "update-one",
                        "label": "应用政策证据",
                        "likelihood_if_true": 0.75,
                        "likelihood_if_false": 0.5,
                        "posterior_probability": 0.5,
                        "evidence_refs": ["evidence-policy"],
                    }
                ],
            }
        }
        validate_brief(self.brief)
        action["probability_tree"]["root"]["updates"][0]["posterior_probability"] = 0.51
        with self.assertRaisesRegex(PublicBriefError, "does not match Bayes calculation"):
            validate_brief(self.brief)

    def test_duplicate_tree_id_is_rejected(self) -> None:
        branches = self.brief["actions"][0]["probability_tree"]["root"]["branches"]
        branches[1]["child"]["id"] = branches[0]["child"]["id"]
        with self.assertRaisesRegex(PublicBriefError, "duplicates ID"):
            validate_brief(self.brief)

    def test_empty_public_site_is_valid(self) -> None:
        data = ROOT / "site" / "data"
        validate_index(load_json(data / "index.json"), data)

    def test_publish_builds_immutable_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            destination = publish(FIXTURE, data)
            self.assertEqual(destination.name, "example-brief.json")
            self.assertEqual(load_json(data / "latest.json")["brief_id"], "example-brief")
            validate_index(load_json(data / "index.json"), data)
            publish(FIXTURE, data)

            changed = copy.deepcopy(self.brief)
            changed["actions"][0]["reason"] = "同一简报标识下的不同有效内容。"
            source = data / "changed.json"
            source.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(PublicBriefError, "refusing to overwrite"):
                publish(source, data)

    def test_schema_forbids_unknown_fields(self) -> None:
        schema = load_json(ROOT / "schemas" / "public-brief.schema.json")
        self.assertFalse(schema["additionalProperties"])
        self.assertFalse(schema["$defs"]["action"]["additionalProperties"])
        self.assertFalse(schema["$defs"]["evidence"]["additionalProperties"])


if __name__ == "__main__":
    unittest.main()
