"""Offline, cold-app ASGI surface qualification. Never starts application lifespans."""

from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import importlib.metadata
import inspect
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

SCRATCH = Path(
    "C:/Users/alphi/AppData/Local/hermes/cache/scratch/agency-build-20261019/daily-routes"
)
EVIDENCE = Path("C:/Users/alphi/agency-evidence/limited-autonomy-20261019/daily-routes")
FACTORIES = {
    "agency": ("api/server.py", "34a929ea9fb5f600638bb469d139964ad9b8dd69ba65060870bee03e08659921"),
    "butler": (
        "butler/server.py",
        "ea265c969323440adbf86171de62885ee016fda7d4ca55caf81f4e9fda998b8f",
    ),
    "prototype": (
        "prototype/app.py",
        "8db5fc43bccff52856762c18c5cc3ee71132661fe2f5ad1bf4a4be059d098bd1",
    ),
    "telegram": (
        "telegram/server.py",
        "fd95518fe3cbe9478d518b506d7cdefb7b39a30e23edf0765458184d9369151a",
    ),
}
PLANNED_API = [
    ("GET", "/api/pilot/capabilities"),
    ("GET", "/api/pilot/threads"),
    ("POST", "/api/pilot/threads"),
    ("GET", "/api/pilot/threads/{thread_id}/messages"),
    ("POST", "/api/pilot/tasks"),
    ("GET", "/api/pilot/tasks"),
    ("GET", "/api/pilot/tasks/{task_id}"),
    ("POST", "/api/pilot/tasks/{task_id}/cancel"),
]
HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


# Immutable archived review baseline, NOT generated from the qualification target.
# Metadata provenance is separate from the executable source/dependency manifest.
BASELINE_METADATA = {
    "source_head": "97c72f29f3fa7d69421e9d0ab72ccf820f3b3ba3",
    "evidence": "limited-autonomy-20261019/daily-routes/final-97c72f2-20261019T190730Z-d8565882",
    "surfaces": 72,
    "cases": 94,
    "safe_surfaces": 28,
    "boundary_only_surfaces": 44,
    "missing_api_pairs": 8,
    "missing_ui": "GET /pilot",
    "exit_code": 2,
}
REVIEWED_SNAPSHOT = {
    "sha256": "8ee783c25eeee14b2a04b68118bc8888a87df8b4a14d3d66c3f6109d91cdb405",
    "files": {
        "src/agency/__init__.py": "19d676aa7a7f9181bb7352386b12268ac6d46b506bd686f9f25cbb9fea90816e",
        "src/agency/addons/__init__.py": "ca2b700e11604c2c61929a6b826381ac3d1177ead1419cb0e158a98492126978",
        "src/agency/addons/dark_factory/__init__.py": "6f74a5433cb62d5e1f00d404e27b460cf0b54d2fbf6fd0ca4f4f95e8aae59f5d",
        "src/agency/addons/dark_factory/absorption.py": "3c863f6002cd0b4327fe851f203dd491d05145f14d951a2666aca4a807ed9acf",
        "src/agency/addons/dark_factory/factory.py": "80f4e5e54b8711acee884fd0b3d2638c499ec559a12af46a5b728b38d54f3cc2",
        "src/agency/addons/dark_factory/sdlc.py": "085e2782bf4e34965772080730ec512abda8027b3fde0f3507327f551f4f2adb",
        "src/agency/addons/ghost_factory/__init__.py": "e51b86006c03c56a0724fab453167f5c6d8d9c12996b914b2218ec827d2be782",
        "src/agency/addons/ghost_factory/factory.py": "84baa345151055c9510f3cf76efaa50a55fb52b0eab7ecd8fc864f627ec1fe83",
        "src/agency/addons/ghost_factory/languages.py": "09c039231c09e9f9239242372a5ef9e01efb1ec38f6be698ed0439d0b329a46e",
        "src/agency/addons/ghost_factory/reverse.py": "07f57a4deafcaaa344bf13cf539dba47edba2432f0149bea6f2a583380ec5239",
        "src/agency/admission/__init__.py": "35af78c0311f5ab733229eb22f636b599c37f3d653e0873e27265bbbf8c6c9ca",
        "src/agency/admission/auth.py": "839fc2593f5cad3c8a7b540499f27bfba4727a7b3dee2c847becedb8193d8ec4",
        "src/agency/admission/budget.py": "c374c2999403f11b786ea956a9e9c1f686aec0a95df7ccc38aacfb4b7072f46e",
        "src/agency/admission/models.py": "041db97425ddae3c8f1ac8015e2513f7f8863189ba7b2c6386e8a553d52a4fb0",
        "src/agency/admission/store.py": "1ce926b4898c6aefc702732573101e90a3f83432e716ac4080acf8c48771acfb",
        "src/agency/agents/__init__.py": "b8846a9a65cdab31cfbe2c0b76cc21f69e67713d207deaeb8e6cd02febf957e8",
        "src/agency/agents/assessment.py": "d85ce8f761d6c707ab7bbb02cd315b996b559f6b6dcc78898e92e058deac8cec",
        "src/agency/agents/demo/__init__.py": "d3aa6d244338bc3e40613996d2a5a76fd2f543d63ceebce5f5963e6718573e13",
        "src/agency/agents/demo/agent.py": "c94195a93eb6ba9cbf30e95d93d9239697313cb4643df5b4b4d8455f4c1bde51",
        "src/agency/agents/executor.py": "b9c4c9143e507d7dda5cfbdac456a94d1f0e72d5fc4fedac0523d709434d2477",
        "src/agency/agents/general/__init__.py": "0588a3dc0a91691e3ccf529ae7d8f4d896b3fee205da0a5f690e6982801dbf16",
        "src/agency/agents/general/agent.py": "6bc0dd291088d5b0a47c9ee45d1a2daddb44623acc9394d8436a16049d4f6e30",
        "src/agency/agents/loop.py": "c4e128dad0b824ca271e5bf8489a4118867832e7025cc9e3a2e3bc60811e5bc2",
        "src/agency/agents/planner.py": "186a009475e382eb57479cea58a84c7c7a3f5966db5976be627ee18a6611bbbb",
        "src/agency/agents/registry.py": "51b8f0dd0d723bb8479bbf72281176642c6981afef9e464c47f900193a5ceaf4",
        "src/agency/agents/research/__init__.py": "30a795b3c19ca7dcacd63ee7485f7d79d6f00e6cd068dde9cdbaec4af73762dc",
        "src/agency/agents/research/agent.py": "487ef9d8bc376c2f3f88615376507ae62654aa8d06ecaae1a07aeaea55a37f25",
        "src/agency/agents/verifier.py": "878655d5b00858b67c4d883d71bfbeb06eedaf36272199b7af416fb2aefb163f",
        "src/agency/api/__init__.py": "72ca1423fd15d91ed41877aec76698c78c2237969025e593eccb9de0da630545",
        "src/agency/api/authority.py": "d272c8fed4fec1583cf37208b73579cc208644114bed40c7a40d61ba76759be0",
        "src/agency/api/routers/__init__.py": "67dfad804df5f8d2e988dcf996151162b282ff927ac6260add1360feda73a717",
        "src/agency/api/routers/agents.py": "9b8b0dd9eb2fa803de6c3a925278ce655431b62e20e5dbd996a3dc03cac470ba",
        "src/agency/api/routers/bridges.py": "2394afa4a583bef9bcee0b5eee3711f641e8ce61ab2e30bbba07856c998507ca",
        "src/agency/api/routers/evidence.py": "423f7b5a4b5900e6ae475094cb6fb43b9f20977a8ad75edea623433034957ecf",
        "src/agency/api/routers/governance.py": "2911fd473c362d4312edc625b63771b78ae63eee5a7c79450f002a83fbfee5ac",
        "src/agency/api/routers/memory.py": "d6678963855053547c2f7224c3bbd454f4299cd7cdacc9580454c6b5612b3f03",
        "src/agency/api/routers/tasks.py": "7fbe6af1dc11f8abff8aaf4316c6b61ebc5b7294c964c3f348c468c4afab87cc",
        "src/agency/api/server.py": "4d9b99494f96c7e38d5b7dc6a2e1c3d59e1cdee62a6fe3bf547a1cc4d12d2062",
        "src/agency/bridges/__init__.py": "284dbcb880c3ec0cf1983f0909aacad06eac76a824bc40390f537e091ac94d09",
        "src/agency/bridges/base.py": "ac8f95d06cb95e398915e2c85cf9b19ba6c96fbee9d2dcf6468490876f1e3a56",
        "src/agency/bridges/claude/__init__.py": "796a8f4a87379952136f0a4a19d34947acd9254d44bff7ac7f7d78d5c7a2c8fc",
        "src/agency/bridges/claude/bridge.py": "6230611c8ffa6da13080f13f731dab2147dd4befadf7f7fc00c4161c5cdd88ce",
        "src/agency/bridges/codex/__init__.py": "b91a66edacada003610736ad3d6bfdfb7e536b7655e1830c2d7573363d172c61",
        "src/agency/bridges/codex/bridge.py": "1f2eaa0acf0f2bfc64fbdf9ffcf5465be6809725f9cf5a931d6863413dbe3ed6",
        "src/agency/bridges/coordinator.py": "0bd7947cb6610f4d2608ca58627abf583ae86327cb92e56488ec6b4405824fec",
        "src/agency/bridges/hermes/__init__.py": "30215b9d2381404e0a9f627bf5afdbe97751e3bc894204a92d02514ad6a6aed9",
        "src/agency/bridges/hermes/bridge.py": "df305f350dee9ef2eeedfca93d0ff1785a1439a81a910a6854439e1b61afc6eb",
        "src/agency/bridges/openclaw/__init__.py": "d223f65a36d43304303a59860b39e67670ee802159b0f1476e958113c3971e4d",
        "src/agency/bridges/openclaw/bridge.py": "3478363631611f5c2da19c317cc460bb788ed8aab6d00b56f29f8b71f385c017",
        "src/agency/butler/__init__.py": "eb5276f3c20869087edc13b6a2da2ce3a2ba2e46281bd1415cefda9ac512c3fb",
        "src/agency/butler/cli.py": "afea99f94aa5190efcdd7df321543a69e3fc696b676e81abffc6d327e904dd26",
        "src/agency/butler/commands.py": "af9d1a43260fe36de557ca7d89506c4493bb85c6a257e5086879f9e303ba684f",
        "src/agency/butler/config.py": "fe16d032b1fa6515e2e29408c4476c2ec0cbe519764902f7515792a39e2873f3",
        "src/agency/butler/context.py": "e7dc9fb1348a679172e66e72813f0bb22d8c10c74518cb2f0560e983d0ec95a1",
        "src/agency/butler/router.py": "73c12ec78106d5b62cd5e25eedf23c0fcdba58e6541c0c4ac468aec31c290432",
        "src/agency/butler/server.py": "35a2d38e0808ffb28063407747c268361f609d45173326e722bbe8bf1eacacf1",
        "src/agency/butler/service.py": "f5ea32ef44e1e87a2be21695fd92e7d2e496dee21f353d2698ecdadc8190c6df",
        "src/agency/butler/threads.py": "899a2f9d6d41776ad583d74a50e880cecd4ab81abb6928dd35924910aa8334f5",
        "src/agency/cli/__init__.py": "95a878d65dd60cf8b2863b8a4f04d94954254fd4c101de5dec0440c7789a06d8",
        "src/agency/cli/main.py": "982d02c2a3b8af9e5ed13047f2ab8327595949f33b042e76da3fd695dec2dc4f",
        "src/agency/config/__init__.py": "5992a4abf74127fbc02de63c0bdac54c551482b3bfdd6f0870d7da8f5920944d",
        "src/agency/config/cli.py": "bee756d230925fc03d7c817fb71ea3083c75d232a703fa7703bbcb2ed8d5746a",
        "src/agency/config/keys.py": "cc4b2769bcdbbb9ab21de4fc3e104ece06ae8c9e5787d4226c99f682d24c58f6",
        "src/agency/config/settings.py": "3da9d55d15c4b8b210472ac9ae259ada0128d11a932bc0b126ad2b2e3f5744b2",
        "src/agency/evidence/__init__.py": "47400f90497f01ebfca74e29ae353375d9cf0f48d5cf22fde7f36e9e13d3cee3",
        "src/agency/evidence/levels.py": "a26badf0a87b1b60ec27f4d060608c556cf853b5dcae032c43b7454d4770f20b",
        "src/agency/evidence/store/__init__.py": "01239491d956e313f67bd6de9c5c54b8a27286329f7e9cb0f917f5feb35fcf17",
        "src/agency/evidence/store/models.py": "e19b62d883904705dbc79b52d869be3b13730f4406b10028923d195d607d069d",
        "src/agency/evidence/store/store.py": "abe2a537d8756dda8ae2c619706e3b112c4f31a247d45b0cbb032fc6aa9543db",
        "src/agency/factory/__init__.py": "1ccca71f327d2c6227586ed58c94f48e196dc97d0d7d0ea4abb67e3764d7aba2",
        "src/agency/factory/service.py": "274504172e7c2d965768c0228bc476f0bd0346e39deea48f48a3ff32826ad0d5",
        "src/agency/forge/__init__.py": "46f8b94983d35898ddaaeaa1d6fc1f88b09fe6fb5b8a40445e431530c9148bda",
        "src/agency/forge/inspection.py": "c96c9f37a1e7e2e7e43810676a4d43a6e2bda42ceb0855cee03d2e1ba62cb64a",
        "src/agency/kernel/__init__.py": "161fc41a8137e121409e7f86321063a6b6e1a6eccdbb39d240746238493a465c",
        "src/agency/kernel/audit.py": "53bfddb129594703f3235007b42b64350a14ec6de8e1060e43008890df871d30",
        "src/agency/kernel/identity.py": "5c855d134104376517dede9b4cf178c8ec591068ba24b7ecf0b130305c7a0a50",
        "src/agency/kernel/policies.py": "b867aff3f8c3ab1af47091ec395d68db158d5de0c46aae8a77c6a4a073935b4f",
        "src/agency/kernel/registry.py": "bc2aa7b64b787dd3a4ae024d7ec29fc35640f02793e35aa9791fcad2a89fb122",
        "src/agency/kernel/tasks.py": "3ed82effb5df7aa45924af4f382818e14e060ff6bfde62e7e9ad44848d6650f2",
        "src/agency/lab/__init__.py": "69cd45781cda9fdd18aa17d5e64300c6aa2a58a71444b5db67baaede9f17819b",
        "src/agency/lab/comet_smoke.py": "c8c6fa5e0976dfe6943599200707b31d031f929530ccb2bc8366d1f290b8d94a",
        "src/agency/lattice/__init__.py": "bb634b8cc6fc766cd41baed510b519fb5d354ab653239c410dfb736c5c3eec84",
        "src/agency/lattice/api.py": "6a981c47afc3526846be4aa77d7c3b6fa7b7b7992918050574ee83282e06a201",
        "src/agency/lattice/backends/__init__.py": "a890dbe39f0fe5b4ebb64a250fdccaea366af1277f70b6b6d49295e787367afc",
        "src/agency/lattice/backends/base.py": "113fda822c42d73d2847cc5087ae91288ec647ae0130577250a4e234f71eb244",
        "src/agency/lattice/backends/sqlite.py": "5d9f1af89cefdcbd4449f09d55c47cae67cf7972190ebf4ebe8d5f0096c2e08b",
        "src/agency/lattice/config.py": "509dc43ee7cf1ce67920ab2ea83715ba891a1362148fd7d22d814de05f9f8a09",
        "src/agency/lattice/factory.py": "b59ee5163c1128177410d40fe02c9e1d0a6283985d7e5b31dc60e62b8bc19c4f",
        "src/agency/lattice/governance.py": "47167ddc6b8fda20c510c85bb2f07716b0207c40772cb768a9d644616f3bb49c",
        "src/agency/lattice/models.py": "1d41644290dc5380f8d55000609ccbe3c53639f152b73b170a5d8a4291868be4",
        "src/agency/lattice/reputation.py": "b7669d171e419562dfd7e1a83ab40bef0a572899fd8e46e24f9da0fe8ce5ffa6",
        "src/agency/llm/__init__.py": "f2c40feba67fcbf2c4d070c72918545b03dd350c4666d98b4ea3cb1fab893982",
        "src/agency/llm/adapter.py": "5b88602223ed2b740c3e5209558ae2cce4e0360e0f29b453b76792e086ed1924",
        "src/agency/llm/config.py": "6216ee3e0a5c8d344d29f5fe07b21734e646284c5baa3c02c3fc351160ef34b6",
        "src/agency/llm/metered.py": "1be811294bfcaf8eec6f2968d48b920a04611435b8a93de77ab5f6b88e3b1d24",
        "src/agency/llm/providers/__init__.py": "6b5827df5a995c71d684a03fc57531f86dd7d579bb3e1d85cf1a4fce6486d9f9",
        "src/agency/llm/providers/anthropic.py": "424f5d7bbad5cd0937509b10b95eb5e97f58fc41b513b729124add8ca481b3c4",
        "src/agency/llm/providers/base.py": "382a8471b381e56640fa06814867fd4fbd0e004445533c45b3dff3539e71def6",
        "src/agency/llm/providers/echo.py": "16bdc4b1e9ff77f4e3e3ee79bc0c6a3e7da1b35f8656307398852ad0035b5d0f",
        "src/agency/llm/providers/ollama.py": "9e94513aaca81eccf52bf4d5083cac644875a94695209ec82151cb110a201959",
        "src/agency/llm/providers/openai.py": "8b5c2958b0fc919dfd42accbb6169986801f574f9e14a92286ed2f36040a30d3",
        "src/agency/llm/router.py": "1c5c25eedee69681252c31c2a876caadc9c73581d298bb3b86c904a771d92883",
        "src/agency/memory/__init__.py": "6d1be600cf02ad8aae131e128eae0f9b558d2296d35e8d6f95c28e5fc50b6487",
        "src/agency/memory/sms/__init__.py": "fc39e4bbdcf23367519ee83ef02045204c4e6375545683460115f820a94eba78",
        "src/agency/memory/sms/cpr.py": "a26ef7bb289c1521f92ff7a3d0846ef3f5234c2d7a16ee479e58e5ee1c7c22e4",
        "src/agency/memory/sms/lifecycle.py": "b89dbd31ae15863bef2df4f0b84c85abd1232d2f8b5dc87231fc3f22bbec760e",
        "src/agency/memory/sms/models.py": "68faffc8046d4f6f58e9f3626a8677d958d5f03a1849cabb63ff2f2d1acbc623",
        "src/agency/memory/sms/retrieval.py": "ceef4981067596a580f990889e3393aecbf5870dee1db7e64a29cc6cf434c911",
        "src/agency/memory/sms/secrets.py": "54d4d4875922339909f89ed1d4cbb63b306e99343bbfdfb2e10497184a8f5f06",
        "src/agency/memory/sms/store.py": "982f0fe31f8767724f9cbee11097b11570a9af83d15cb2d3dc349ac11fe00801",
        "src/agency/orchestrator.py": "a00498e6da4f4fbf1201bf42f946b7618e09a87b60ce9cf029e2c21fd3ab244f",
        "src/agency/peer_envelope.py": "02350d3a854fe3f604ed21b001e0ca1ba4c8ccaa8c4b342ce4370feb9288ddc0",
        "src/agency/prototype/__init__.py": "f41659264bf10b3b79fcd42c28b6babc6fa806f75ce97e13fe55307c120b9bd8",
        "src/agency/prototype/app.py": "139372e383ac750050e846d0c9f20fd9d9df28644d6d74cdbc8d051f1324ab47",
        "src/agency/prototype/static/app.js": "34408cef6e13c2ab29d7cf795bd872a3c677bbe72c9ede4f776817f62723ab7f",
        "src/agency/prototype/static/index.html": "cb0179f3611db5739645c4cf88e79f44dc7e4711d9aa0304186feb01401985f5",
        "src/agency/prototype/static/pilot.css": "5184355d44dfd1f987280559aaad14467770077d82bc9bd3b3799421070de793",
        "src/agency/prototype/static/pilot.html": "d38f51a67980c6ba8db5e651cdacdbda45bd11c7c4530642843421e1f24a3aeb",
        "src/agency/prototype/static/pilot.js": "71a8ca2a2cdb92c4933a6a6fed81ad05e85df864c7c72962246b1b6340d07ea2",
        "src/agency/prototype/static/style.css": "62633abd08962d571c55e947494d7fdb3919dea4cc9c16c36081e6c7665a35f9",
        "src/agency/risk/__init__.py": "1ec300dd6bab438a68f117d07c7048063d716840b686b9f4dec8ae1f9f29f00a",
        "src/agency/risk/engine/__init__.py": "58a580fb900e0d7e5db3e8befe68ee7c7d0925fb9dbe7b0dc88828df6b689b03",
        "src/agency/risk/engine/engine.py": "fb2041290a47190d6a2fa1467759183b9d8c9ef5d92730c1f35bbf4c37f67e09",
        "src/agency/risk/engine/models.py": "77cc0dd56c670bf5b6f7dc70eb04f939775839105ace5509251840254d01494f",
        "src/agency/risk/scoring.py": "320adc73ea9b6da84b9bd3df592221457be9d3511d4f67df9a670c868878eafe",
        "src/agency/security/__init__.py": "be3acaa8f24339ea47a243e0f374670d1a3d3e29f525a87434078ecf6820f50d",
        "src/agency/security/blue/__init__.py": "390e439ebb57b4298222f203e10bb85c33794383b7f94b2c98520d909a650125",
        "src/agency/security/blue/defender.py": "29052c034e2ac6149edde51f97c42e3fbb62c12e0a5783bf32396042ae7134d2",
        "src/agency/security/purple/__init__.py": "03cf24a1473f08190d874e210c2078f262083fee9186a2d6874c75ddd2145352",
        "src/agency/security/purple/validator.py": "e57d285b2f09c93a70a499277c730c9a439a40629e35536a539806e9de159a44",
        "src/agency/security/red/__init__.py": "0c7486f4e96fc6015bea82656fa062b620baa28e1e248372e67274e3407fb9a7",
        "src/agency/security/red/executor.py": "886440e1072da1a8c2d638cef21f1239462114a789a169b2934d1b3d0246b0e3",
        "src/agency/security/red/planner.py": "6acdb78b2b514d97d02f89945ab8e3454faef46ada70ba8aa78096750da87237",
        "src/agency/security/sandbox/__init__.py": "fce78fafc0232d586cec612d74eae9fedb7eeb68a9932b0f1c0a718d0903adc9",
        "src/agency/security/sandbox/config.py": "320fb7340082ad56cf0ab3871a24b2be1d9db57d85c2e79e064b55615cb5d76b",
        "src/agency/security/sandbox/manager.py": "b9d46b860a557ec2cdd72913e96a8edb96986beb8a9ca7f2c4771596307aa81e",
        "src/agency/security/sandbox/process.py": "cf1de3c4803578bfd15f05ed54fd3809369a8cbe3abc0f3edb7a643009fd7096",
        "src/agency/skills/__init__.py": "a20753cd2582274b687ae8ace55c8674057ab7e04f931c6806dcc3792a162d2c",
        "src/agency/skills/registry.py": "47cf9b724fbb3e6de850aa62c4a7ddbb3935dbb225d23a8d714763f5d86fab16",
        "src/agency/telegram/__init__.py": "d25f563f0af7162afd6350ed8c9007cb123e11732a5c12ddf1794a7bff9405a1",
        "src/agency/telegram/adapter.py": "4212fd011947c0ae603910567d904d8311e214048d55aec6e23842e5d726e582",
        "src/agency/telegram/budget_store.py": "61839e9ba27e4dd1d36461e925fcdd3449a62cdcb97151bf51b6314908992f3e",
        "src/agency/telegram/config.py": "9d098220d5bea9388290785febd02f250a5c47910737e5b00fe05276d0da33d7",
        "src/agency/telegram/handler.py": "5ca87e6ffd5caec5cb901b00c65214ad166806ca5bf07a6749a44f805be4c48c",
        "src/agency/telegram/profile_store.py": "fcc40b04f3a490ccde66dd38ddc8fcebbffb10c8b3f26be73969458d6455f7f3",
        "src/agency/telegram/rate_limit.py": "75d8a9514eef91b004c4f61cc0690aa485a2af84c2548cb6f56d83fb340d9a65",
        "src/agency/telegram/server.py": "4b92b3cd83c9a0f87537984400ef1dc51cfb88f8f23dbd2aa8cf47fd6adadef6",
        "src/agency/tools/__init__.py": "bde9ca9e4ce370fb060601ff9d2a9a4b01fed21d1b4fa656c852a3151cb80443",
        "src/agency/tools/base.py": "1c58ec9a62402c6d89d46e6d090eb6c87e07139935f6910838c8972944f668f3",
        "src/agency/tools/builtin/__init__.py": "9ec13ff17d2a2606f39537791a1ed87580e1e4014483b9358f2ded54e13a5296",
        "src/agency/tools/builtin/calculator.py": "88e063175004337e429bca1ee3dbcda4a38c857d96fb21081572961ebcf48777",
        "src/agency/tools/builtin/memory.py": "d00c50981adc64d8488f9be8fe80fb3207dca5ba9cbc21dc76eb2307a59c8c06",
        "src/agency/tools/builtin/sandbox.py": "a83abbed842901ac404af2080b429feaaf2aa67e798b8ae486a4c882affd6766",
        "src/agency/tools/builtin/utc_time.py": "579d4010af919e0d90490ab63d7fd767144e0316bff49407f00a4acf0a9e88f8",
        "src/agency/tools/builtin/web.py": "c8e83e67caa2fcea43236d5145f5f9982cb6d09e112387345ec97309833fc81f",
        "src/agency/tools/driver.py": "f915b360f47a917dc1d2ce41026eece059c9251c4feae818691282ced00b6d23",
        "src/agency/tools/registry.py": "5ce957b8aa3f6b7713b637aff947c52bab47244c482fd105471fd256f0a0d4c1",
        "src/agency/workflows/__init__.py": "239b67dbe00d9cd24d54aa5032fc5ff1dd408709cfe895ab8fb3823a92193980",
        "src/agency/workflows/executor.py": "1ef0170a5ea525527b28235c84c249ea09f68f1a45e6e06f30810ffac0451d93",
        "src/agency/workflows/recovery.py": "948e3a089f6ab0a54b6c052f1925be4e2e50dad14f407ca2ae72a8d6dac059b3",
        "src/agency/workflows/registry.py": "52201133e9b6713e841d60016eb5d8b271e75bc8b685afdec85aafc3e028e765",
        "src/telegram_bot.py": "4035f334fc212165c7bfcc1f6bc466f9e13a5ea8167bf6a9dd4a73a157d498bb",
        "pyproject.toml": "4302645bf56499e14cf7f4b2b55b179f8f920036c5be871feea4ef11854f0ef3",
        "uv.lock": "b39929c0782d1095646823d3232f8a0485e167ae9f760bb68dd84ee784bf4867",
        "docs/SPEC_OWNER_PILOT_M2_M3_20261019.md": "c073056fcb9740ed17b2f56dee8c71b528b5fe723385809e58b310f41858ac93",
    },
}
REVIEWED_VERSIONS = {
    "fastapi": "0.141.1",
    "starlette": "1.7.0",
    "httpx": "0.28.1",
    "pydantic": "2.13.5",
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def surface_id(app: str, method: str, path: str) -> str:
    return f"{app} {method} {path}"


def inventory_app(name, app):
    """Union effective FastAPI contexts + generated schema + static file expansion."""
    from fastapi.routing import iter_route_contexts
    from starlette.routing import Mount
    from starlette.staticfiles import StaticFiles

    schema = app.openapi()
    schema_keys = {
        (m.upper(), p)
        for p, item in schema.get("paths", {}).items()
        for m in item
        if m in HTTP_METHODS
    }
    entries = {}
    route_keys = set()
    schema_expected = set()
    mounts = []
    unsupported = []

    def add(method, path, kind, **extra):
        key = (method, path)
        row = entries.setdefault(
            key,
            {
                "id": surface_id(name, method, path),
                "app": name,
                "method": method,
                "path": path,
                "kinds": [],
            },
        )
        if kind not in row["kinds"]:
            row["kinds"].append(kind)
        row.update(extra)
        return row

    for context in iter_route_contexts(app.routes):
        original = context.original_route
        path = context.path
        if path is None:
            unsupported.append({"type": type(original).__name__, "reason": "no HTTP path"})
            continue
        if isinstance(original, Mount):
            mount = add(
                "*",
                path + "/{path:path}",
                "mount",
                name=context.name,
                mounted_type=type(original.app).__name__,
            )
            mounts.append(mount["id"])
            if isinstance(original.app, StaticFiles):
                mount["handler_methods"] = ["GET", "HEAD"]
                mount["other_method_status"] = 405
                mount["html"] = original.app.html
                mount["directories"] = [
                    str(Path(d).resolve()) for d in original.app.all_directories
                ]
                for directory in original.app.all_directories:
                    root = Path(directory).absolute()
                    for file in sorted(code_paths(root, static=True)):
                        relative = file.relative_to(root).as_posix()
                        for method in ("GET", "HEAD"):
                            add(
                                method,
                                path + "/" + relative,
                                "static-file",
                                mount_id=mount["id"],
                                sha256=digest(file.read_bytes()),
                                size=file.stat().st_size,
                            )
            else:
                unsupported.append(
                    {
                        "type": type(original.app).__name__,
                        "path": path,
                        "reason": "non-static child mount not expanded",
                    }
                )
            continue
        methods = context.methods
        if not methods:
            unsupported.append(
                {
                    "type": type(original).__name__,
                    "path": path,
                    "reason": "not an HTTP method route",
                }
            )
            continue
        for method in sorted(methods):
            key = (method, path)
            route_keys.add(key)
            in_schema = getattr(context, "include_in_schema", False)
            if in_schema:
                schema_expected.add(key)
            kind = "schema-route" if key in schema_keys else "hidden-or-generated"
            add(
                method,
                path,
                kind,
                name=context.name,
                route_type=type(original).__name__,
                include_in_schema=bool(in_schema),
                in_openapi=key in schema_keys,
            )
    for method, path in sorted(schema_keys):
        add(method, path, "openapi", in_openapi=True)
    return {
        "app": name,
        "surfaces": sorted(entries.values(), key=lambda r: (r["path"], r["method"])),
        "generated_openapi": schema,
        "openapi_only": [surface_id(name, *k) for k in sorted(schema_keys - route_keys)],
        "route_only_schema_expected": [
            surface_id(name, *k) for k in sorted(schema_expected - schema_keys)
        ],
        "unsupported": unsupported,
        "mounts": mounts,
        "raw_route_types": dict(Counter(type(r).__name__ for r in app.routes)),
        "effective_context_count": sum(1 for _ in iter_route_contexts(app.routes)),
    }


METADATA_FILES = ("pyproject.toml", "uv.lock", "docs/SPEC_OWNER_PILOT_M2_M3_20261019.md")
STATIC_SUFFIXES = {".html", ".js", ".css", ".svg", ".png", ".ico", ".woff", ".woff2"}
RUNTIME_DIRECTORIES = {"data", "runtime", "logs", "cache", ".cache"}


def sensitive_name(name):
    lower = name.lower()
    return lower.startswith(".env") or bool(re.search(r"\.(?:db|sqlite3?)(?:[-.].*)?$", lower))


def no_links(path):
    """lstat each component before traversal/open; also reject Windows junctions."""
    import stat

    for component in reversed((path, *path.parents)):
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
        ):
            raise ValueError("Source/static links require separate containment review")


def code_paths(directory, *, static=False):
    """Classify the entire tree BEFORE reading bytes; never follow a directory link."""
    no_links(directory)
    result = []
    with os.scandir(directory) as entries:
        for entry in sorted(entries, key=lambda e: e.name):
            path = Path(entry.path)
            no_links(path)
            if sensitive_name(entry.name) or entry.name.lower() in RUNTIME_DIRECTORIES:
                raise ValueError("Sensitive/runtime path is not qualification source")
            if entry.is_dir(follow_symlinks=False):
                if entry.name != "__pycache__":
                    result.extend(code_paths(path, static=static or path.name == "static"))
            elif entry.is_file(follow_symlinks=False):
                if path.suffix == ".pyc":
                    continue
                if not (path.suffix.lower() in STATIC_SUFFIXES if static else path.suffix == ".py"):
                    raise ValueError("Unreviewed file type is not qualification source")
                result.append(path)
            else:
                raise ValueError("Special files are not qualification source")
    return result


def source_snapshot(root):
    root = Path(root).absolute()
    paths = code_paths(root / "src")
    for relative in METADATA_FILES:
        path = root / relative
        # lstat/no_links precedes is_file; dangling metadata links are rejected too.
        if path.exists() or path.is_symlink():
            no_links(path)
            if not path.is_file():
                raise ValueError("Metadata must be a regular file")
            paths.append(path)
    files = {}
    for path in sorted(paths):
        no_links(path)
        files[path.relative_to(root).as_posix()] = digest(path.read_bytes())
    return {"sha256": digest(json.dumps(files, sort_keys=True).encode()), "files": files}


def git_metadata(root):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()

    return {
        "root": str(root),
        "head": git("rev-parse", "HEAD"),
        "branch": git("branch", "--show-current"),
        "status_porcelain": git("status", "--porcelain=v1"),
    }


def configure_environment(run):
    # Do not inherit arbitrary provider settings, credentials, database paths or proxies.
    keep = {
        k: v
        for k, v in os.environ.items()
        if k.upper() in {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "SYSTEMDRIVE"}
    }
    os.environ.clear()
    os.environ.update(keep)
    data = run / "data"
    temp = run / "temp"
    data.mkdir(parents=True)
    temp.mkdir()
    os.environ.update(
        {
            "HOME": str(run),
            "USERPROFILE": str(run),
            "APPDATA": str(run),
            "LOCALAPPDATA": str(run),
            "TEMP": str(temp),
            "TMP": str(temp),
            "TMPDIR": str(temp),
            "PYTHON_DOTENV_DISABLED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "AGENCY_LLM_PROVIDER": "echo",
            "AGENCY_LLM_MODEL": "echo",
            "AGENCY_BETA_MODE": "false",
            "BUTLER_LLM_PROVIDER": "disabled",
            "BUTLER_LLM_MODEL": "echo",
            "LATTICE_BACKEND": "inmemory",
            "LATTICE_SQLITE_PATH": str(data / "lattice.db"),
            "AGENCY_EVIDENCE_DB": str(data / "evidence.db"),
            "AGENCY_MEMORY_DB": str(data / "memory.db"),
            "REMEX_PROFILE_DB_PATH": str(data / "profiles.db"),
        }
    )
    tempfile.tempdir = str(temp)
    sys.dont_write_bytecode = True
    os.chdir(run)
    return temp


def install_guard(run, output):
    """Python audit containment, not an OS sandbox; no children are permitted."""
    denied = []
    write_roots = (run.resolve(), output.resolve())

    def inside(path, roots):
        return any(path == root or root in path.parents for root in roots)

    def reject(event, reason):
        denied.append({"event": event, "reason": reason})
        raise PermissionError(f"Qualification guard: {reason}")

    def guard(event, args):
        if event in {
            "socket.connect",
            "socket.bind",
            "socket.getaddrinfo",
            "socket.sendto",
            "subprocess.Popen",
            "os.system",
            "os.exec",
            "os.spawn",
            "os.fork",
        }:
            reject(event, "network/process operation forbidden")
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(args[0])).resolve()
            if path.name.lower().startswith(".env"):
                reject(event, "dotenv access forbidden")
            if re.search(r"\.(?:db|sqlite3?)(?:[-.].*)?$", path.name.lower()) and not inside(
                path, (run.resolve(),)
            ):
                reject(event, "non-scratch database access forbidden")
            mode, flags = args[1], args[2]
            writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                isinstance(flags, int)
                and bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
            )
            if writing and not inside(path, write_roots):
                reject(event, "write outside run/evidence forbidden")
        if (
            event == "sqlite3.connect"
            and args[0] != ":memory:"
            and not inside(Path(args[0]).resolve(), (run.resolve(),))
        ):
            reject(event, "non-scratch sqlite connection forbidden")
        if event in {"os.mkdir", "os.remove", "os.rmdir", "os.rename"}:
            targets = args[:2] if event == "os.rename" else args[:1]
            for value in targets:
                if isinstance(value, (str, bytes, os.PathLike)) and not inside(
                    Path(os.fsdecode(value)).resolve(), write_roots
                ):
                    reject(event, "filesystem mutation outside run/evidence forbidden")

    sys.addaudithook(guard)
    return denied


def reviewed_factory(root, relative, expected):
    tree = ast.parse((root / "src/agency" / relative).read_text(encoding="utf-8"))
    factory = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "create_app"
    )
    actual = digest(ast.dump(factory, include_attributes=False).encode())
    return {
        "expected_ast_sha256": expected,
        "actual_ast_sha256": actual,
        "reviewed_baseline_match": actual == expected,
    }


def planned_coverage(surfaces):
    present = {(r["method"], r["path"]) for r in surfaces if r["app"] == "prototype"}
    return [
        {
            "method": method,
            "path": path,
            "status": "present-unqualified" if (method, path) in present else "MISSING",
            "expected_status": "implemented route required; absence is never a passing 404",
        }
        for method, path in [*PLANNED_API, ("GET", "/pilot")]
    ]


def coverage_report(surfaces, cases):
    ids = {r["id"] for r in surfaces}
    executed = {
        sid for c in cases if c.get("actual_status") is not None for sid in c["surface_ids"]
    }
    passed = {sid for c in cases if c["passed"] for sid in c["surface_ids"]}
    direct = {
        sid
        for c in cases
        if c["passed"] and c["scope"] == "safe-handler"
        for sid in c["surface_ids"]
    }
    return {
        "inventory_count": len(ids),
        "executed_surface_count": len(ids & executed),
        "passed_case_surface_count": len(ids & passed),
        "safe_handler_surface_count": len(ids & direct),
        "unexercised": sorted(ids - executed),
        "not_directly_handler_qualified": sorted(ids - direct),
        "unknown_case_surface_ids": sorted(executed - ids),
        "case_count": len(cases),
        "passed_cases": sum(c["passed"] for c in cases),
        "failed_cases": sum(not c["passed"] for c in cases),
    }


def peer_denial_matches(response):
    # HTTP HEAD deliberately has no response body, including on middleware denials.
    if response.request.method == "HEAD":
        return len(response.content) == 0
    return response.json() == {"detail": "Peer authorization is unavailable."}


def safety_preflight(root):
    """Complete reviewed source/dependency admission, before any Agency import."""
    try:
        actual = source_snapshot(root)
        expected = REVIEWED_SNAPSHOT["files"]
        observed = actual["files"]
        changed = sorted(p for p in expected.keys() & observed.keys() if expected[p] != observed[p])
        missing = sorted(expected.keys() - observed.keys())
        added = sorted(observed.keys() - expected.keys())
        versions = {p: importlib.metadata.version(p) for p in REVIEWED_VERSIONS}
        return {
            "approved": actual == REVIEWED_SNAPSHOT and versions == REVIEWED_VERSIONS,
            "review": "embedded archived baseline",
            "expected_sha256": REVIEWED_SNAPSHOT["sha256"],
            "actual_sha256": actual["sha256"],
            "snapshot": actual,
            "changed": changed,
            "missing": missing,
            "added": added,
            "expected_versions": REVIEWED_VERSIONS,
            "actual_versions": versions,
        }
    except (OSError, ValueError, RuntimeError, importlib.metadata.PackageNotFoundError) as exc:
        return {"approved": False, "reason": type(exc).__name__}


def unqualified_receipt(output, metadata, preflight):
    result = {
        "source": metadata,
        "completed_at": datetime.now(UTC).isoformat(),
        "baseline_metadata": BASELINE_METADATA,
        "safety_preflight": preflight,
        "execution": {
            "runtime_ready": False,
            "agency_import_attempted": False,
            "application_lifespans": False,
        },
        "cases": [],
        "bounded_cases_passed": False,
        "daily_driver_qualified": False,
        "exit_code": 2,
        "gaps": [
            "Source/dependencies require explicit safety re-review before Agency import or construction."
        ],
    }
    (output / "receipts.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"evidence": str(output), "safety_preflight": preflight, "exit_code": 2}))
    return 2


async def qualify(root, output, run, metadata, before):
    # The loop is already constructed: on Windows its self-pipe must predate the socket guard.
    denied = install_guard(run, output)
    preflight = safety_preflight(root)
    if not preflight["approved"]:
        return unqualified_receipt(output, metadata, preflight)
    before = preflight.pop("snapshot")
    sys.path.insert(0, str(root / "src"))
    from contextlib import AsyncExitStack

    import fastapi
    import httpx
    from fastapi.routing import iter_route_contexts

    import agency
    from agency.api.server import create_app as agency_app
    from agency.butler.config import ButlerConfig
    from agency.butler.server import create_app as butler_app
    from agency.prototype.app import create_app as prototype_app
    from agency.telegram.config import TelegramConfig
    from agency.telegram.handler import TelegramHandler
    from agency.telegram.profile_store import ProfileStore
    from agency.telegram.server import create_app as telegram_app

    imported_source = Path(agency.__file__).resolve()
    if not imported_source.is_relative_to(root / "src"):
        raise RuntimeError("Imported Agency from the wrong checkout")
    reviews = {name: reviewed_factory(root, *values) for name, values in FACTORIES.items()}
    cases = []
    all_inventory = {}
    skipped = []
    started = metadata["invoked_at"]
    # Only real transport/profile resources; no fake backend service and no product handler patches.
    async with AsyncExitStack() as stack:
        config = TelegramConfig(
            bot_token="synthetic-not-live", webhook_secret="synthetic-not-live", beta_mode=False
        )
        profiles = ProfileStore(":memory:")
        try:
            handler = TelegramHandler(config, profile_store=profiles)
        except BaseException:
            profiles.close()
            raise
        stack.push_async_callback(handler.close)
        apps = {
            "agency": agency_app(),
            "butler": butler_app(
                ButlerConfig(_env_file=None, llm_provider="disabled", llm_model="echo")
            ),
            "prototype": prototype_app(),
            "telegram": telegram_app(config, handler),
        }
        for name, app in apps.items():
            all_inventory[name] = inventory_app(name, app)
        surfaces = [row for inv in all_inventory.values() for row in inv["surfaces"]]
        by_id = {r["id"]: r for r in surfaces}
        assert len(by_id) == len(surfaces), "inventory deduplication failed"
        clients = {
            name: await stack.enter_async_context(
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(
                        app=app, client=("127.0.0.1", 32100), raise_app_exceptions=False
                    ),
                    base_url="http://127.0.0.1",
                    follow_redirects=False,
                    trust_env=False,
                )
            )
            for name, app in apps.items()
        }

        async def case(
            name,
            method,
            path,
            expected,
            *,
            label,
            scope="boundary-only",
            ids=None,
            check=None,
            **kwargs,
        ):
            row = {
                "case_id": f"{name}:{label}",
                "app": name,
                "method": method,
                "path": path,
                "expected_status": expected,
                "scope": scope,
                "surface_ids": ids if ids is not None else [surface_id(name, method, path)],
                "actual_status": None,
                "passed": False,
            }
            if any(sid not in by_id for sid in row["surface_ids"]):
                raise RuntimeError(
                    "Case refers to an absent surface; do not qualify missing routes"
                )
            response = None
            try:
                response = await clients[name].request(method, path, **kwargs)
                row.update(
                    actual_status=response.status_code,
                    body_sha256=digest(response.content),
                    body_bytes=len(response.content),
                    content_type=response.headers.get("content-type"),
                    location=response.headers.get("location"),
                    allow=response.headers.get("allow"),
                )
                content_ok = True if check is None else bool(check(response))
                row.update(
                    content_check=content_ok, passed=response.status_code == expected and content_ok
                )
            except Exception as exc:  # noqa: BLE001 - record failures without hiding the remaining cases
                row["exception_type"] = type(exc).__name__
            cases.append(row)
            return response

        # Review pins ensure later trees cannot accidentally turn closed probes into privileged operations.
        for name in ("agency", "butler"):
            if not reviews[name]["reviewed_baseline_match"]:
                skipped.append(
                    {
                        "app": name,
                        "reason": "factory changed: re-review before closed-route requests",
                    }
                )
                continue
            for row in all_inventory[name]["surfaces"]:
                method, path = row["method"], row["path"]
                concrete = re.sub(r"\{[^}]+\}", "synthetic-unknown", path)
                is_health = method == "GET" and path == "/v1/health"
                expected = 200 if is_health else 503
                check = (
                    (lambda r: r.json()["status"] in {"degraded", "starting"})
                    if is_health
                    else peer_denial_matches
                )
                await case(
                    name,
                    method,
                    concrete,
                    expected,
                    label=f"declared:{method}:{path}",
                    ids=[row["id"]],
                    scope="safe-handler" if is_health else "middleware-denial",
                    check=check,
                    **(
                        {"content": b"{broken", "headers": {"Content-Type": "application/json"}}
                        if method not in {"GET", "HEAD"}
                        else {}
                    ),
                )
            health_id = surface_id(name, "GET", "/v1/health")
            await case(
                name,
                "POST",
                "/v1/health",
                503,
                label="invalid-method-health",
                ids=[health_id],
                scope="middleware-denial",
            )
            await case(
                name,
                "GET",
                "/v1/health/",
                503,
                label="trailing-slash-no-bypass",
                ids=[health_id],
                scope="middleware-denial",
            )

        if reviews["prototype"]["reviewed_baseline_match"]:
            name = "prototype"
            for path in ("/api/health", "/api/agents", "/api/session", "/"):
                checks = {
                    "/api/health": lambda r: (
                        r.json() == {"status": "starting", "butler": "starting", "agents": 0}
                    ),
                    "/api/agents": lambda r: r.json() == {"agents": []},
                    "/api/session": lambda r: bool(r.json().get("csrf_token")),
                    "/": lambda r: "text/html" in r.headers.get("content-type", ""),
                }
                await case(
                    name,
                    "GET",
                    path,
                    200,
                    label=f"safe-read:{path}",
                    scope="safe-handler",
                    check=checks[path],
                )
            chat_id = surface_id(name, "POST", "/api/chat")
            csrf = apps[name].state.csrf_token
            headers = {"X-Prototype-CSRF": csrf}
            await case(
                name, "POST", "/api/chat", 403, label="missing-csrf", json={"message": "synthetic"}
            )
            await case(
                name,
                "POST",
                "/api/chat",
                403,
                label="foreign-origin",
                json={"message": "synthetic"},
                headers={**headers, "Origin": "https://attacker.invalid"},
            )
            await case(
                name,
                "POST",
                "/api/chat",
                422,
                label="forged-sender",
                json={"message": "synthetic", "sender": "forged-owner"},
                headers=headers,
            )
            await case(
                name,
                "POST",
                "/api/chat",
                422,
                label="empty-message",
                json={"message": ""},
                headers=headers,
            )
            await case(
                name,
                "POST",
                "/api/chat",
                415,
                label="wrong-content-type",
                content="synthetic",
                headers={**headers, "Content-Type": "text/plain"},
            )
            await case(
                name,
                "POST",
                "/api/chat",
                503,
                label="no-running-butler",
                json={"message": "synthetic-not-dispatched"},
                headers=headers,
                check=lambda r: r.json() == {"error": "Butler service is unavailable."},
            )
            for path in ("/api/health", "/"):
                await case(
                    name,
                    "GET",
                    path,
                    403,
                    label=f"foreign-host:{path}",
                    headers={"Host": "attacker.invalid"},
                )
            await case(
                name,
                "GET",
                "/api/chat",
                405,
                label="invalid-method-chat",
                ids=[chat_id],
                check=lambda r: "POST" in r.headers.get("allow", ""),
            )
            health_id = surface_id(name, "GET", "/api/health")
            await case(
                name,
                "GET",
                "/api/health/",
                307,
                label="health-slash-redirect",
                ids=[health_id],
                check=lambda r: r.headers.get("location") == "http://127.0.0.1/api/health",
            )
            for row in all_inventory[name]["surfaces"]:
                if "static-file" in row["kinds"]:

                    def static_check(r, row=row):
                        return (
                            digest(r.content) == row["sha256"]
                            if row["method"] == "GET"
                            else len(r.content) == 0
                        )

                    await case(
                        name,
                        row["method"],
                        row["path"],
                        200,
                        label=f"static:{row['method']}:{row['path']}",
                        ids=[row["id"], row["mount_id"]],
                        scope="safe-handler",
                        check=static_check,
                    )
            for mount_id in all_inventory[name]["mounts"]:
                mount = by_id[mount_id]
                prefix = mount["path"].split("/{path:path}")[0]
                await case(
                    name,
                    "GET",
                    prefix,
                    307,
                    label=f"mount-slash:{prefix}",
                    ids=[mount_id],
                    check=lambda r, prefix=prefix: (
                        r.headers.get("location") == "http://127.0.0.1" + prefix + "/"
                    ),
                )
                await case(
                    name,
                    "GET",
                    prefix + "/synthetic-absent.txt",
                    404,
                    label=f"static-missing:{prefix}",
                    ids=[mount_id],
                )
                await case(
                    name,
                    "POST",
                    prefix + "/app.js",
                    405,
                    label=f"static-invalid-method:{prefix}",
                    ids=[mount_id],
                )
            # Absent docs are configuration probes, not successes for planned routes.
            for path in ("/docs", "/redoc", "/openapi.json"):
                await case(name, "GET", path, 404, label=f"docs-disabled:{path}", ids=[])
        else:
            skipped.append(
                {
                    "app": "prototype",
                    "reason": "factory changed: prototype/API guards need re-review; no dispatch attempted",
                }
            )

        if reviews["telegram"]["reviewed_baseline_match"]:
            name = "telegram"
            for row in all_inventory[name]["surfaces"]:
                if row["method"] not in {"GET", "HEAD"}:
                    continue
                path = row["path"]
                if path == "/openapi.json" and row["method"] == "GET":
                    check = lambda r: r.json() == apps[name].openapi()
                elif path in {"/docs", "/redoc", "/docs/oauth2-redirect"}:
                    check = lambda r: "text/html" in r.headers.get("content-type", "")
                else:
                    check = lambda r: r.json() == {"status": "ok", "service": "telegram-webhook"}
                if row["method"] == "HEAD":
                    check = lambda r: len(r.content) == 0
                await case(
                    name,
                    row["method"],
                    path,
                    200,
                    label=f"safe-read:{row['method']}:{path}",
                    scope="safe-handler",
                    check=check,
                )
            webhook_id = surface_id(name, "POST", "/telegram/webhook")
            await case(
                name,
                "POST",
                "/telegram/webhook",
                403,
                label="auth-before-malformed-json",
                content=b"{broken",
            )
            await case(
                name,
                "POST",
                "/telegram/webhook",
                400,
                label="authenticated-malformed-json",
                content=b"{broken",
                headers={"X-Telegram-Bot-Api-Secret-Token": config.webhook_secret},
            )
            await case(
                name,
                "POST",
                "/telegram/webhook",
                400,
                label="authenticated-nonobject-json",
                json=[],
                headers={"X-Telegram-Bot-Api-Secret-Token": config.webhook_secret},
            )
            config.webhook_secret = None
            await case(
                name,
                "POST",
                "/telegram/webhook",
                503,
                label="disabled-secret-before-parse",
                content=b"{broken",
            )
            await case(
                name,
                "GET",
                "/telegram/webhook",
                405,
                label="invalid-method-webhook",
                ids=[webhook_id],
            )
        else:
            skipped.append(
                {"app": "telegram", "reason": "factory changed: webhook guards need re-review"}
            )

    import sqlite3

    profile_closed = False
    try:
        profiles._conn.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        profile_closed = True
    cleanup_observed = {
        "all_asgi_clients_closed": all(client.is_closed for client in clients.values()),
        "synthetic_profile_closed": profile_closed,
        "telegram_transport_not_open": handler._adapter._client is None,
    }
    after = source_snapshot(root)
    inventory = {
        "source": metadata,
        "source_fingerprint": before,
        "safety_preflight": preflight,
        "baseline_metadata": BASELINE_METADATA,
        "apps": all_inventory,
        "surfaces": surfaces,
        "planned_routes": planned_coverage(surfaces),
        "counts_by_app": dict(Counter(r["app"] for r in surfaces)),
        "factory_safety_review": reviews,
        "router_iteration_implementation": inspect.getsource(iter_route_contexts),
        "fastapi_version": fastapi.__version__,
    }
    coverage = coverage_report(surfaces, cases)
    inconsistent = any(
        i["openapi_only"] or i["route_only_schema_expected"] or i["unsupported"]
        for i in all_inventory.values()
    )
    missing = [p for p in inventory["planned_routes"] if p["status"] == "MISSING"]
    result = {
        "started_at": started,
        "completed_at": datetime.now(UTC).isoformat(),
        "source": metadata,
        "safety_preflight": preflight,
        "baseline_metadata": BASELINE_METADATA,
        "source_sha256_before": before["sha256"],
        "source_sha256_after": after["sha256"],
        "source_unchanged": before == after,
        "harness_sha256": digest(Path(__file__).read_bytes()),
        "interpreter": sys.executable,
        "versions": {
            p: importlib.metadata.version(p) for p in ("fastapi", "starlette", "httpx", "pydantic")
        },
        "execution": {
            "transport": "httpx.ASGITransport; actual ASGI app, synthetic loopback peer",
            "application_lifespans": False,
            "runtime_ready": False,
            "cold_app_fixture": True,
            "synthetic_profile_store": ":memory:",
            "model": "explicit echo configured, no model dispatch intended",
            "cwd": str(run),
            "temp": tempfile.gettempdir(),
            "containment": "Python audit hook; blocks all network/process calls, dotenv/database access and writes outside scratch/evidence; not an OS sandbox",
            "cleanup": "AsyncExitStack closed all HTTP clients and real Telegram handler/profile; no app lifespans started",
        },
        "cleanup_observed": cleanup_observed,
        "guard_denials": denied,
        "cases": cases,
        "coverage": coverage,
        "skipped": skipped,
        "planned_missing_count": len(missing),
        "planned_missing": missing,
        "planned_unqualified": [
            p for p in inventory["planned_routes"] if p["status"] != "qualified"
        ],
        "planned_unqualified_count": sum(
            p["status"] != "qualified" for p in inventory["planned_routes"]
        ),
        "inventory_inconsistent": inconsistent,
        "bounded_cases_passed": coverage["failed_cases"] == 0
        and not denied
        and before == after
        and not inconsistent
        and all(cleanup_observed.values()),
        "daily_driver_qualified": False,
        "gaps": [
            "No pilot service/API qualification: planned missing/present-unqualified routes are not passing cases.",
            "No real provider, live HTTP server, application lifespan/startup or ready runtime exercised.",
            "Middleware denials exercise the boundary, not underlying business handlers.",
            "No arbitrary resource IDs, authenticated valid webhook updates, owner tasks or model requests executed.",
        ],
    }
    (output / "inventory.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    (output / "receipts.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "evidence": str(output),
                "source_head": metadata["head"],
                "source_sha256": before["sha256"],
                "counts_by_app": inventory["counts_by_app"],
                "coverage": coverage,
                "planned_missing_count": len(missing),
                "bounded_cases_passed": result["bounded_cases_passed"],
                "daily_driver_qualified": False,
            },
            indent=2,
        )
    )
    if not result["bounded_cases_passed"]:
        return 1
    return (
        2
        if any(p["status"] != "qualified" for p in inventory["planned_routes"])
        or coverage["unexercised"]
        or skipped
        else 0
    )


def main():
    invoked_at = datetime.now(UTC).isoformat()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--label", default="qualification", help="Non-sensitive evidence prefix")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", args.label):
        parser.error("label must be a short non-sensitive alphanumeric identifier")
    root = args.source_root.absolute()
    if not (root / "src/agency").is_dir():
        parser.error("source root must contain src/agency")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    run = SCRATCH / stamp
    output = EVIDENCE / (args.label + "-" + stamp)
    run.mkdir(parents=True)
    output.mkdir(parents=True)
    original_cwd = Path.cwd()
    # Read-only git subprocesses finish before the audit guard prohibits child processes.
    metadata = git_metadata(root)
    metadata["invoked_at"] = invoked_at
    before = None
    configure_environment(run)
    try:
        return asyncio.run(qualify(root, output, run, metadata, before))
    except Exception as exc:
        (output / "fatal.json").write_text(
            json.dumps(
                {
                    "exception_type": type(exc).__name__,
                    "source": metadata,
                    "source_sha256": before["sha256"] if before else None,
                    "completed": False,
                }
            ),
            encoding="utf-8",
        )
        raise
    finally:
        os.chdir(original_cwd)


if __name__ == "__main__":
    raise SystemExit(main())
