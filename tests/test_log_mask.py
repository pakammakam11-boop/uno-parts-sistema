"""S1: žetonai neturi patekti į žurnalą."""
import logging

from app import main


def test_mask_tokens_in_access_log(caplog):
    logger = logging.getLogger("uvicorn.access")
    logger.propagate = True
    with caplog.at_level(logging.INFO, logger="uvicorn.access"):
        logger.info('%s - "%s %s HTTP/1.1" %d', "1.2.3.4", "GET", "/hr/login/SECRETTOKEN?x=1", 200)
        logger.info("GET /hr/sign/TOK123/7 200")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "SECRETTOKEN" not in text and "TOK123" not in text
    assert "/hr/login/***" in text and "/hr/sign/***/7" in text


def test_dockerfile_disables_access_log():
    from pathlib import Path
    assert "--no-access-log" in (Path(main.__file__).parent.parent / "deploy" / "Dockerfile.app").read_text(encoding="utf-8")
