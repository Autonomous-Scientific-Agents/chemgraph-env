"""Calculator examples and acceptance cases shared by validation and handoff."""

DEFAULT_MODELS = {"mace_mp": "medium-mpa-0", "mace_polar": "polar-1-m"}
REQUIRED_CALCULATIONS = {
    "emt", "tblite", "mace-default", "mace-named",
    "mace-polar-default", "mace-polar-named",
}


def examples(profile):
    common = {"device": profile["device"], "default_dtype": profile["dtype"]}
    return {
        "mace": {"calculator_type": "mace_mp", "model": DEFAULT_MODELS["mace_mp"], **common},
        "mace_polar": {"calculator_type": "mace_polar", "model": DEFAULT_MODELS["mace_polar"],
                       "charge": 0, "multiplicity": 1, "external_field": [0.0, 0.0, 0.0], **common},
        "tblite": {"calculator_type": "tblite", "method": "GFN2-xTB"},
        "emt": {"calculator_type": "emt"},
    }


def acceptance_cases(profile):
    recipes = examples(profile)
    return {
        "emt": recipes["emt"],
        "tblite": recipes["tblite"],
        "mace-default": {k: v for k, v in recipes["mace"].items() if k != "model"},
        "mace-named": recipes["mace"],
        "mace-polar-default": {k: v for k, v in recipes["mace_polar"].items() if k != "model"},
        "mace-polar-named": recipes["mace_polar"],
    }
