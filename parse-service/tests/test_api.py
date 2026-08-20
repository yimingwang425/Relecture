"""``POST /parse`` 的接口测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from parse_service.app import MAX_UPLOAD_BYTES, app

CORPUS = Path(__file__).resolve().parents[2] / "实验脚本" / "测试集"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def upload(client: TestClient, name: str, data: bytes):
    return client.post("/parse", files={"file": (name, data, "application/octet-stream")})


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["schema_version"]


@pytest.mark.skipif(not CORPUS.exists(), reason="语料不存在")
class TestHappyPath:
    def test_digital_courseware(self, client):
        path = CORPUS / "MIT11_201F07_slides2.pdf"
        response = upload(client, path.name, path.read_bytes())
        assert response.status_code == 200
        body = response.json()
        assert body["document"]["verdict"] == "usable"
        assert body["document"]["page_count"] == 15
        assert body["pages"][0]["blocks"]
        # 解析与切片一次返回：服务无状态，没有「先存起来再切」的中间态
        assert body["slices"]["manifest"]["slice_count"] == 15
        assert body["slices"]["manifest"]["content_sha256"] == body["document"]["content_sha256"]

    def test_scanned_courseware_reported_unusable(self, client):
        """PRD §10：扫描件在上传时就要被识别出来。"""
        path = CORPUS / "MIT15_316SU05_leader_dev.pdf"
        body = upload(client, path.name, path.read_bytes()).json()
        assert body["document"]["verdict"] == "unusable"
        assert body["document"]["usable_page_count"] == 0
        assert len(body["document"]["unusable_pages"]) == 19
        assert body["slices"]["slices"] == []
        assert len(body["slices"]["manifest"]["skipped_pages"]) == 19

    def test_partially_unusable_still_returns_200(self, client):
        """放行并告知，不替用户拒绝——14.129 有 4 页图页，仍应正常返回。"""
        path = CORPUS / "MIT14_129S25_lec02.pdf"
        body = upload(client, path.name, path.read_bytes()).json()
        assert body["document"]["verdict"] == "usable"
        assert body["document"]["unusable_page_count"] == 4
        assert [u["kind"] for u in body["document"]["unusable_pages"]] == ["image_only"] * 4
        # 页号连续性：跳过 p33-p36 后不重新编号
        pages = [s["page_indices"][0] for s in body["slices"]["slices"]]
        assert 32 in pages and 37 in pages and 33 not in pages

    def test_no_cross_user_dedup_key(self, client):
        """PRD §12.1：服务不产出文档主键，内容哈希只作校验与同用户内重复提示。"""
        path = CORPUS / "MIT11_201F07_slides2.pdf"
        body = upload(client, path.name, path.read_bytes()).json()
        assert "doc_id" not in body["document"]
        assert "content_sha256" in body["document"]


class TestRejections:
    def test_pptx_is_not_yet_implemented(self, client):
        """501 而非 415：区分「不支持」与「还没做」，避免调用方去做永久兜底。"""
        response = upload(client, "lecture.pptx", b"PK\x03\x04stub")
        assert response.status_code == 501
        assert "PPTX" in response.json()["detail"]

    def test_unsupported_suffix(self, client):
        assert upload(client, "notes.docx", b"stub").status_code == 415

    def test_empty_file(self, client):
        assert upload(client, "empty.pdf", b"").status_code == 400

    def test_corrupt_pdf(self, client):
        response = upload(client, "broken.pdf", b"this is not a pdf at all")
        assert response.status_code == 422

    def test_truncated_pdf(self, client):
        response = upload(client, "cut.pdf", b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog")
        assert response.status_code == 422

    def test_oversized_file(self, client):
        response = upload(client, "big.pdf", b"%PDF-1.7" + b"\x00" * MAX_UPLOAD_BYTES)
        assert response.status_code == 413

    def test_missing_file_field(self, client):
        assert client.post("/parse").status_code == 422
