def custom_install_cmd(instance_id):
    """
    Custom install commands for specific instance IDs.

    Attributes:
        instance_id (str): The ID of the instance.
    Returns:
        list[str] or None: List of custom install command strings if applicable, else None.
    """
    if instance_id == "instance_001":
        return ["echo 'custom for 001'", "pip install -e ."]
    if instance_id.startswith("exp_"):
        return ["pip install experimental-pkg"]
    # SWE-smith oauthlib: the vendored swesmith spec install ("pip install -e . && pip
    # install pyjwt") is INCOMPLETE — it omits the crypto/signals optional deps the repo's
    # RSA / signed-token / signal test suite needs (oauthlib declares these only in
    # extras_require: rsa=[cryptography], signedtoken=[cryptography,pyjwt], signals=[blinker]).
    # The canonical SWE-smith Docker image baked them in; the github-mirror install does not,
    # so ~22 PASS_TO_PASS RSA/JWT tests fail and reward never reaches 1. Install the full set.
    # (Built once on the login node with network; pods reuse the resulting venv offline.)
    if instance_id.startswith("oauthlib__oauthlib"):
        return [
            'python -m pip install -e . && '
            'pip install "cryptography>=3.0.0" "pyjwt>=2.0.0,<3" "blinker>=1.4.0"'
        ]
    # if None, we fall back to default install commands
    return None
def custom_test_cmd(instance_id):
    """
    Custom test commands for specific instance IDs.
    
    Attributes:
        instance_id (str): The ID of the instance.

    Returns:
        str or None: Custom test command string if applicable, else None.
    """ 
    if instance_id == "instance_002":
        return "echo 'setup for 002'"
    # if None, we fall back to default setup commands
    return None

