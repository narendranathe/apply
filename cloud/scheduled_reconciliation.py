"""Pinned recovery of the October 18 application failure and three guard stops.

This operation never executes business lanes or providers. Snapshot and delta
pins bind it to the actual authenticated captures and independently reviewed
records for this incident only.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import io
import json
from pathlib import Path
import re
import tempfile
import zipfile

from cloud.cutover import validate_cutover_record, validate_import
from cloud.notion import AmbiguousWriteError
from cloud.outreach import record
from cloud.records import content_hash, validate_record
from cloud.startup_reconciliation import (
    BASELINE_HASH, BASELINE_RECORDS, READY_CORE_HASH, REPOSITORY,
    ReconciliationBlocked, SnapshotState, _complete_list, _time, core,
    require, sha, source_metadata,
)
from scripts.encrypted_backup import restore_archive, safe_name

SCHEMA = 'apply-pinned-scheduled-writer-resolution/v1'
SLOT_SCHEMA = 'apply-pinned-scheduled-slot-resolution/v1'
SLOT = 'run:2026-10-18:applications'
FAILED = '39135562489'
FAILURES = (FAILED, '39137302215', '39293466126', '39295904880')
PINNED_COMMIT = 'b3c845f84760088594635db46b6e3bfca2098391'
BEFORE = '39084147465'
BEFORE_COMMIT = '71ead8d5d4d5ca1e443ce1bb455301294627006d'
BEFORE_CIPHER_HASH = 'eb05e5230fa3be34b01c43c6c5d86f16c56da5e552f90d645eb96f67cd91ae86'
BEFORE_FINGERPRINT = '3784e27b2564d87a79e411f39a018cb820eb77f4549440e3389f7786f47b4d35'
BEFORE_PAGES = 2071
AFTER = '39317412711'
# These are fixed reviewed deployment pins, never workflow/user inputs.
AFTER_CIPHER_HASH = '88fb6d5dce537cb794f001c71ad780834978f25fa821f677fc39b10e38d92864'
AFTER_FINGERPRINT = 'd7e842c955ce8c5e7024e3805982fb6ef39e14967ce01a5aa14b8b3e3de219c2'
AFTER_PAGES = 2143
ADDED_RECORD_CORE_HASHES = {
    "job_observation:490d5ae304cc6cd768616d173ae2139c5379adc9071cdd6009b1fd1ca42e6d2f": "551cbcf5e53d28117e77599d5b25980fe355d759554d685f7b0c7350ab607f7f",
    "job_observation:5668e93b978ea9203bb676ef38ad46ec27322aab0506f5f1fe436301cba2b934": "e42abd62b6b2fe896675a1de83cbee798159982b8905e0f382cd04b059f88603",
    "job_observation:84070cda7d11933b728b9081ac949044aaf6ada4daac43f4f518aefafef78a17": "d1a34c274346d7ec8befe684a425d25a4d0ab2ef670121cd70f904911f1f10ea",
    "plan:084823281b257d314875815b643002156d9e0b09dc4ddbf37b64d89d1587f2ca": "4e8ba954fd0d90961c8e40b5947531945df4f3afd5f0c5ff721ab634c8286e22",
    "plan:0956070687e00d82d4ea49b352bc58e9ab9fdd59a67ed823ed325335dcfc4a85": "fe9f7d6fd7ca5bcf028b66cbaa7d3e6fcfb4b946b343b53b923430aaee6321c6",
    "plan:0e05cf9fe15ecd73a63c0a7b5d15cad36355ea1c5ddbeb3e817aca72f4dc9c07": "bc9b4108fdcbfed9c5c3016481b196b5304c1317cf0fd15a9c4ab57d0dd5eab0",
    "plan:15be190f0b6290f87f536fd11756d57f5fd1e80207bb9603b69c92c25c659724": "6fa9ed8c9e7375e159a9c9d328c2a0171f56abe871efbb0807570aaf28b77709",
    "plan:15d40d071049120fbc5faa5a7f51ea5f70785c0b3d76581ccaa3e860f9236099": "db3d50e1b6286f69a5c05723a50f771271724b0bb0c9bad556c8a5b35f34fef7",
    "plan:1cef0c0c68462fdea99b61e194de8a486a00ce40d6386631fd95e5c48f7db1f8": "896b8836db918e222d9069a978753f7f3e16aecf3f35f7eba6c56ccc58ebd3e5",
    "plan:1d985aa89afb3650db8fdb0762243f1609f46414f06daa3d0f595ba4d97f39c7": "31945452b3df53c141b6a387da514f483a41d12a6ebb39be7b194d46aa2f770d",
    "plan:2815a468c7fad8966ad1e81766f81c6f42f5c5fb15e917466e6f16378a8d0a2d": "d320ab00901b6a4706c4c81d62e3498b1c1190b07931b79ed855edfeea7279eb",
    "plan:296ffb54991017498e2b9095920afefc7a579f43b5d4ea997e7d879f4c6334cd": "cbc510621db151b0d6d0e7ae510198da18fb97bda0b373501a53b90855d954b6",
    "plan:29f911e95b8b7ebc9e5196e38d3b6fd47fdf5483557ae0f9cf2a2726fd3535aa": "356014d61d698c67ec418c6afe2749235c552529eb43b7792a2561c2fc6b65af",
    "plan:2f6a062bc8c01cb5d96f5b8c5ac9946a19969005a067730b1e9ea865e4d23b2f": "3c079910e5e2be72765dbaa8d73dfc207ac54c74ef5ac38adb0e25454b179a6d",
    "plan:3a94f6812446ac9bf0a81f70659fe346f4d0ca77e67cefb733345bd5f016e326": "5615f9877118147245b867aedf05eb02ffbf89d67f7c677a864cd022c63b9b5a",
    "plan:40c4fa397f739cbac3cc76dd97a76512386df2bfef0f09349295938816ac8ad5": "2514e2ab823441d1057a2ee9b3ed3405315995f72283e5fd237d29fa5290f6e8",
    "plan:441b0234bab9b73adffa69b3561ebfed8cf8607de48b92ddc5217545570721ef": "72f2e54c9bb96b0b1b0993f66e13f4ee739a3ae442217238d8ae822a366f8034",
    "plan:4ba6900c15d795d7248a538a54c4b6208ee879a285ea098487731f53474eb0fe": "66699e30d861bf2fe45acc9508eea98f8efcd2a94aa6c012c16876ecf089c1f6",
    "plan:4f5acb3d3349a35bf549ceb24da135c4ca3198fb81fa5a1218fb0f70513ab66f": "9d97bedef4f05d1f3652d4bdd1d7f84f85ae47a4693eae6e5c8e4538fb49be0e",
    "plan:54142f6b0618e8dab7b632598ec65c0604354d1cf01ace60ab456b8468adba20": "30bc49a0dc3f31fdb99b821915f47f1f6a6d87f4f7158c4cf84551009d10efd8",
    "plan:57d01ea93c5a6d920c8ebb0429aee6c3066bedbea499448232635951eb235c5c": "8537da031bd66f0313267d3f8d14f6c62f920d3563f7830bf06d950b4e350fa1",
    "plan:5b54c133cbb8a96fadefd0b47d50219f2f179c97c9cf85e7906fb428c9af830e": "1b0e0c54b07ab44fc7d6d637f6fa0c408e33a65767bc15e6e80d0fada3a5c0fd",
    "plan:5b5b0701499602873f57039ea6bcd8950c3934e9bda490fa693a86bdfc3bdfd3": "39cd79d4834f24f2a7a50eedcf79130b3a55fa3503f0ff8c836c32ab1f7f7aaa",
    "plan:5d2966149ecaf7bae64444bd55eb494c5ab2b05fd608dfe0c5bb030c9a0e8f4e": "973f1131619db4c79b5c36c70647d4e641f52a4360f283c468f44f0b89e2b0cf",
    "plan:6bb6b21c34668ef264890f2c25d2aa259251d9161bcd06d527bff85c28d6d838": "a2baf0f8f6067478090876c6dd656cc14e02dbc132b4872b0af9e8a052e39240",
    "plan:703aaa283506216815c269d84e74ed354a99616694e25c78468da30a3b6bb30c": "3d3d79c4d1d8aabb77b1b95b5ccc7cc9c87e0152acd861d31ca3dc9eb27ec523",
    "plan:742245a547bfd2c4d68a7db67bcb4dc727c1947627a6e7ae95a9e0b8ec322ea5": "794f839ab651eb0c00aa3a65c25e48522c363a66c4cb437e9ce3e5f4a869e1db",
    "plan:744bd66d9e931de321cabcb1e8f5ff6ee55398c930634fffb9d4b545316a2924": "ef4445b5d098ad6b74d6aeb88fda78745c6dd24864583659eefc1d8e7efe53ce",
    "plan:77100e55f12f07b18e44f310882001148bbcbeb096fb57685c0be2b2468aa62b": "09c4a773ea920232ca0e1b59e551dd6a947d8a1a7ab8f4dbb2ba852dda0ae138",
    "plan:7ce1d5698b0bddefea4643dd127e6a8fdf1aad2b741567fd0591ae68bd21ee4c": "921edd8e8d157b79936258e3f609c07be1305bb87342a4d33ef42ccae215cc73",
    "plan:85e4b6bc3698a8711b91be5ba806c2c0ef30e0677d078965962b9810ecc9cb4d": "d134b41ab441f46bdd97fb6ab1aa404a8e1a6d30ff317a3bf6a74c08e1b2d6dc",
    "plan:87091ee4c781aae05ade2186c2b4989a3ac5b2739a61811c72a54d658afbb4b7": "c0c6758be1dfec3e8f25902c933875874e50a03c920eb4e6a5077b0d287a6501",
    "plan:8adbdc56b4b7539660c022367970a9cfdc39117ee0e18693610976dd060427b7": "8febbda48010dd82c3f7ca133d3330be93c9c952782ad4cd2078a640b7b712bf",
    "plan:8c3d71c496cc554057e7b319cb05aa7a16b31ea0a5048c4f350d1b5a0103d6ca": "cb26bf69b623b2961235620cc57d8b5f4031760de6a1f55fabc23fc5b3d8841d",
    "plan:902f6411baea7bf479c7ae5467a1cd3a42acbcd847eae77ee1107281c8c4a475": "7ef9bcb66739d3e97f96bd40c4ef52f32842ca2f3fee7eaee694598f2c3d196b",
    "plan:91c144d968ab6dca9b8c44b6963e0cba0577e97d68367f0848622d6ac62f99d8": "1fb7ea1e354036d6b0074f10e4b0b761e79b3e80ce52cc95121afc075bb84dbe",
    "plan:924c69ea64052c25ae119c3f9a93cd6a6af3dfdd7b98bd338f1b0fbbe42bb7c2": "c3d3b008a1218df0a24ac78016038f903c5afd4e9ed7678b1e1b451d704633c1",
    "plan:97fc9344cd132baffe44cbda1a055586c06fb55c1871c0c169e2898b44e3f618": "e94169ad0de836288ba066652b7621529954c58f32c947d55c658daabe7745c9",
    "plan:a69a1dc115933fda8af45d926a2cbc719d2732980ef4c3eea761f626615e90db": "4bb9ca2d9fc08c13c85c3486cdfa6385cfc7abfca9627cca14f2647e9799c973",
    "plan:b62f14cce452b8f277545beb6d44593894272df7d874c1668130eb8dd5d085a5": "a69059eb5f8b9261a4542ed49a0c20d8914878524b867b45a720a9b97270b016",
    "plan:b757d2b60648284a7eaaf617ab5416a06e3d9bbf6a902defc3b0b76466ebaa53": "b94f1b90706e993115488970d1a1a64dba90250fd26faf0b99b786c9a70481a0",
    "plan:b91f9d8ad88954cb83621f88220e7da165fa1854a72a4a63ab49e7e28ce07d09": "ad1bba14a58f62ff4bed8b899a0303741df2a5bb5e1a384d7f6cbcc2d7b8ad71",
    "plan:b935811da9eec125c578ba7a61085452754562ce15ac6a3043f7afd59206cc80": "a1505d9f096763cc94ff002046cdc26d719757b894d4698200ac0638c42460d0",
    "plan:ba4b90cca91659004ac1ae5e2a038b3f1a4cc5c7b791a8f7f3029e283f928139": "2157afc8fc1fdb5a5da3ce58471000e2be3b4955bab411b9c2238b7cf2d1ca71",
    "plan:c21245238345cd9d64f95c57a2027767e78c3ef489888f2eb4ceade5c7e3972b": "578446ccf43e3bd803bf80d387051334e89fbbab7bfb2d4897851b71cec17e3e",
    "plan:c355b3177cffe631bcd475ff09b0afebe4bb64097627e9cb7ec7ca7f04556ce5": "9c877e3ca36d41684ec9c569249132950cf008dcefa1f72d0654581ed7144f7f",
    "plan:c9e877c1603b2c5917f918ee25580b7b689f40d81f77a3352a36b7bedd02b22f": "a9e364d415517c35f1f3bfb254c6896ef81e21e1bbddbc76d7b5c241c436be73",
    "plan:cc9520e82752730f3af02f41fe44ff0f0394cb197886f00cc4944be7b8c5bc2e": "7e02fccc324c1fb1fe5af48ab801ce21e63758e61f0f550e1d0ac13e7db91fcc",
    "plan:ceb29b8027fe913c66bdf6b225b3f4ff76a7fc9290d684131af60a7b3d4a7b8f": "8f5bb9cc2694c40399b64751292afda909fa62cf2d1928ef4eb737e2df280302",
    "plan:d01dd92e22013e9b93a6002de0b7185588deb75ce73e1891f665a8ebcdf2d942": "66302d00bfa72afcd6621b97a6636f94c493d979106772eb8fde0dab1f72e931",
    "plan:d3d8bb526a32ac1a14ac0e4bfe9c0d80dc52a939751145ea8208affeff07be20": "dac2161a2899c704566c4c6d7260255f15723dda6e17e5d834a1a7b6236ee151",
    "plan:d56189de76746d0a080c74fe0a987229c79cd555724f5717845b708daf50fe7a": "30248026a32ada2f733ff6e072efc42653bb4a2cd36d00ee35cc9ec3fb933511",
    "plan:dd0870b9190615a657c675b32354bb3a79833e704bf922576bdd31fd3c809eb7": "08481df28381d908592691de80f2967848af134edc3672f51848807cf4e7aff0",
    "plan:ddc26db279377660d6a216713b0530a6d7599e224c3ebf803223617532b40fed": "34091597ab90e9315ca6e7a2e620911fda42b26acfd4c849a1aec2769073b053",
    "plan:de5e21dddf75fdc15793ebbd1772426113eeca3c3b8bcbdf069d4c1827119432": "8fe7985e34ebecb5054e383bed99ca450176e1a38e0b239112210fe4fc786d6a",
    "plan:e10303cd8d1e5c3eae1a72763c8619fb9597a5c6a77472c8e08fd30b42d27462": "cae5783ace81738391108b123a6a913fc84116b158e2fa5a0d1cb40c1a906f69",
    "plan:e31d5096f5318c3bcef710b2a0936140d11e0959af9dfe5508507eddcc840d7d": "d9cbd9a321ad1f11dc739435d52b957371683b8560b6811c5b4bcb90ba3a5faa",
    "plan:e3851dea28cb74701a8c501713a6be2cae41b0bf5dff29dc9e39168a37715714": "e05dab459e071090f20ac9e05e7f3ca13f056facae77dd4f1d9774d4574c297f",
    "plan:e82a8e137337eec31789a0dd6401d0c19ec42fb022d43c0e7089704fc081131f": "9c284c11a5fd00e3fab04f8b32d82526de32327781cc379adebcd2af7341e5d5",
    "plan:ea092ade96240fda2fdadd5bce3cb7c1a01b55da2d7146717e932cbe3f909a15": "18a325b80275aff7c657d55cb6ab9a6cb3299aaf2010fe46bab4a3adb84ff7ff",
    "plan:f41b8178d60b8c6ddf5ea54675d0cd041e013c003d89aa9699a3dd94e0f1d099": "69b55dc26b820dffa3df481aa1e655dd07a9d438b7f5433bde968aeb5034eb43",
    "plan:fb87f23eff03ddd32b35780516255a370bfbdcfcea7da385517f74263b02a4b3": "1b81a74e021e58853b668d7a112c215346551c3684df259e8dac1febcbd7efb2",
    "plan:ff7ce23a4125f92a5c78a66581f94a472feec947603076856329443771699694": "bbaf51f562e38e29438fe559655c0149d355339ef10de821c2518d4a0cc89a7d",
    "role_route_plan:16e856ea48bc2a48123324c1d8362bdc5a2927eb73ad534d5f6b2a5c70d31ff6": "47cc7f9f25f459f3e01d3010ecd6ec434065fa77ef56627694a5c74d2e69f150",
    "role_route_plan:27dd7f1597e41c1deccdc124831983bb284da4917b6ed3a7aeee5a3042f3659a": "45d16aa3ac28d891be3c3c4140b2a866cb48e6dc8e151dca0c5b34991bbc3398",
    "role_route_plan:381c8f29105fd0e81a500be887f987a96e99aa21112e0da2d45de609496abd10": "b141eb9c3b650f273bde0d14e1ac7a170d74b3761f26546cd08ec702c67a2191",
    "run:2026-10-18:accepted:completed": "63d62a66b7a45fda6d06311f6d8e969d7908d11bffc4825bad5a4f9fb6bba31b",
    "run:2026-10-18:accepted:started": "cb996cf9e96b9c9e82fd21051649e92f6b80c8f43f5c2b493b7a618c5c35e65b",
    "run:2026-10-18:applications:dead_letter": "510de66fc74dd8e23399c6ce78a6e8a58e0f630d3c2d5206656faf68a9f5f2b3",
    "run:2026-10-18:applications:started": "05d72654a1c491f3abaf5e11fac62d6292cbbda2609fa3d8e589247537ff1cae",
    "run:2026-10-18:first_touch:completed": "f77d4b2c3fec94f3b36e3a5c27829eebc3fd89eb61b9423a2150345c4d6d29b1",
    "run:2026-10-18:first_touch:started": "354458f17ca28607a3334719c231e5286e90e0647e68e1fdaa359058b9ae7610"
}
SOURCE_HASHES = {
    ".github/workflows/cloud-operations.yml": "85ee5287db4846e7cb13f94c6436b24ee0dd418fe5e20cbf0e0e709075bc7dd2",
    "cloud/applications.py": "259dda6adf9c3a1685a368355dfe614f1827b397466fe75de82531b9605aaa36",
    "cloud/discovery.py": "772c317c0f95eb6b065834f9a2e08e89e1e0c2bfb3334debfad5ae5b91a2fd73",
    "cloud/orchestrator.py": "bd645d245ef6dadb08b3c4dae05a0aa45f9b5875eb01ade4ebc32da16df7b18d",
    "cloud/outreach.py": "0af9dfa0fa00ad173b68f3bcc11bd08e001c0b8441eb1e7887f5cac01fc90f68",
    "cloud/preparation.py": "0619f3de4da66a7763928c9324ab1cc111568e60eeb3c068ba6ef6550606ee77",
    "cloud/submission.py": "3b8bf9c922cf30008393784ad996c590d319b45582157bc4276b33566ed5ffa7",
    "config/cloud.json": "102aec4611c61704a6a0be172c1d03451bbdfea0adeae44b0ad73fb50f76f52d"
}
RUN_FIELDS = ('id', 'display_title', 'head_sha', 'head_branch', 'status', 'conclusion',
              'event', 'run_attempt', 'path', 'updated_at', 'created_at', 'run_started_at')
TITLES = dict(zip(FAILURES, ('cloud:live:0 6 * * *', 'cloud:live:30 6 * * *',
                           'cloud:live:0 6 * * *', 'cloud:live:30 6 * * *')))
DIAGNOSTICS = {identity: ({'status': 'blocked', 'error_type': 'StateConflict',
    'reason': 'inspect_private_reconciliation_records', 'cloud_operation': 'run_applications'}
    if identity == FAILED else {'status': 'blocked', 'error_type': 'Blocked',
                               'reason': 'unresolved_previous_writer_run'}) for identity in FAILURES}


def _hash(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def require_reviewed_pins():
    require(_hash(AFTER_CIPHER_HASH) and _hash(AFTER_FINGERPRINT)
            and type(AFTER_PAGES) is int and AFTER_PAGES > BEFORE_PAGES
            and isinstance(ADDED_RECORD_CORE_HASHES, dict) and bool(ADDED_RECORD_CORE_HASHES)
            and all(isinstance(key, str) and key and _hash(value)
                    for key, value in ADDED_RECORD_CORE_HASHES.items()), 'scheduled_snapshot_review_pending')


def expected_slot_identity():
    return SLOT


def verify_maintenance_context(github, context):
    identity = context.get('run_id', '')
    require(re.fullmatch('[0-9]+', str(identity)) is not None, 'maintenance_run_identity_missing')
    run = github.fetch('/actions/runs/' + str(identity))
    require(str(run.get('id')) == str(identity) and run.get('head_sha') == context['commit']
            and run.get('head_branch') == 'main' and run.get('status') == 'in_progress'
            and run.get('conclusion') is None and run.get('event') == 'workflow_dispatch'
            and type(run.get('run_attempt')) is int and run['run_attempt'] == 1
            and run.get('display_title') == 'history:reconcile-scheduled'
            and run.get('actor', {}).get('login') == 'narendranathe'
            and run.get('triggering_actor', {}).get('login') == 'narendranathe'
            and str(run.get('path', '')).partition('@')[0] == '.github/workflows/history-reconciliation.yml',
            'central_maintenance_run_mismatch')
    jobs = github.fetch('/actions/runs/' + str(identity) + '/jobs?filter=all&per_page=100&page=1')
    require(_complete_list(jobs, 'jobs', 1), 'maintenance_jobs_incomplete')
    job = jobs['jobs'][0]
    require(job.get('name') == 'reconcile' and job.get('status') == 'in_progress'
            and job.get('conclusion') is None and 'ubuntu-latest' in job.get('labels', [])
            and 'self-hosted' not in job.get('labels', [])
            and sum(step.get('name') == 'Reconcile without replaying historical effects'
                    and step.get('status') == 'in_progress' and step.get('conclusion') is None
                    for step in job.get('steps', [])) == 1, 'central_maintenance_job_mismatch')
    source = github.fetch('/contents/.github/workflows/history-reconciliation.yml?ref=' + context['commit'])
    try:
        body = base64.b64decode(source['content'], validate=False)
        text = body.decode('utf-8')
    except Exception:
        raise ReconciliationBlocked('maintenance_workflow_source_unavailable') from None
    require(source.get('encoding') == 'base64'
            and body == Path('.github/workflows/history-reconciliation.yml').read_bytes()
            and len(re.findall(r'^concurrency:\s*$', text, re.MULTILINE)) == 1
            and re.search(r'^concurrency:\n  group: hiring-outreach-single-writer\n  cancel-in-progress: false\n', text, re.MULTILINE)
            and re.search(r'(?:if|elif) \[ "\$IMPORT_MODE" = \'reconcile-scheduled\' \]; then\n            python scripts/reconcile_scheduled.py', text),
            'central_writer_concurrency_contract_mismatch')


def _diagnostics(logs):
    require(isinstance(logs, bytes) and 0 < len(logs) <= 2_000_000, 'scheduled_log_unavailable')
    diagnostics = []
    try:
        for line in logs.decode('utf-8').splitlines():
            if '{' not in line:
                continue
            try:
                value = json.loads(line[line.index('{'):])
            except ValueError:
                require('"status"' not in line, 'scheduled_log_invalid')
                continue
            if isinstance(value, dict) and 'status' in value:
                diagnostics.append(value)
    except UnicodeError:
        raise ReconciliationBlocked('scheduled_log_invalid') from None
    return diagnostics


def _run_proof_valid(identity, proof):
    return (identity in FAILURES and isinstance(proof, dict) and set(proof) == set(RUN_FIELDS)
            and type(proof.get('id')) is int and str(proof['id']) == identity
            and proof['display_title'] == TITLES[identity] and proof['head_sha'] == PINNED_COMMIT
            and proof['head_branch'] == 'main' and proof['status'] == 'completed'
            and proof['conclusion'] == 'failure' and proof['event'] == 'schedule'
            and type(proof['run_attempt']) is int and proof['run_attempt'] == 1
            and isinstance(proof['path'], str)
            and proof['path'].partition('@')[0] == '.github/workflows/cloud-operations.yml'
            and all(_time(proof[key]) is not None for key in ('created_at', 'run_started_at', 'updated_at')))


def verify_incidents(github):
    sources = {}
    for path, expected in SOURCE_HASHES.items():
        source = github.fetch('/contents/' + path + '?ref=' + PINNED_COMMIT)
        try:
            body = base64.b64decode(source['content'], validate=False)
        except Exception:
            raise ReconciliationBlocked('pinned_source_unavailable') from None
        require(source.get('encoding') == 'base64' and sha(body) == expected, 'pinned_source_contract_mismatch')
        sources[path] = body
    # Source bytes above bind the production CLI, no injection, read-only packet
    # preparation and the permission gate before every provider submission.
    require(json.loads(sources['config/cloud.json']).get('applications', {}).get('employers') == {},
            'submission_impossibility_contract_changed')
    result = {}
    names = ['Set up job', 'Run actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683',
             'Run actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065',
             'Run pip install -r requirements.txt', 'Install isolated PDF build tools',
             'Verify private destination', 'Run one coordinated operation']
    for identity in FAILURES:
        path = '/actions/runs/' + identity
        run = github.fetch(path)
        proof = {key: run.get(key) for key in RUN_FIELDS}
        require(_run_proof_valid(identity, proof), 'pinned_failed_run_mismatch')
        jobs = github.fetch(path + '/jobs?filter=all&per_page=100&page=1')
        require(_complete_list(jobs, 'jobs', 1), 'failed_job_history_incomplete')
        job = jobs['jobs'][0]
        require(type(job.get('id')) is int and job['id'] > 0 and job.get('name') == 'orchestrate'
                and job.get('status') == 'completed' and job.get('conclusion') == 'failure'
                and 'ubuntu-latest' in job.get('labels', []) and 'self-hosted' not in job.get('labels', []),
                'failed_hosted_job_mismatch')
        steps = job.get('steps')
        require(isinstance(steps, list) and all(isinstance(step, dict) for step in steps)
                and len({step.get('number') for step in steps}) == len(steps), 'failed_steps_incomplete')
        by_number = {step.get('number'): step for step in steps}
        require(all(by_number.get(n, {}).get('name') == name
                    and by_number[n].get('status') == 'completed'
                    and by_number[n].get('conclusion') == ('failure' if n == 7 else 'success')
                    for n, name in enumerate(names, 1))
                and [step.get('number') for step in steps if step.get('conclusion') == 'failure'] == [7],
                'failed_operation_steps_mismatch')
        logs = github.download_path('/actions/jobs/' + str(job['id']) + '/logs')
        require(_diagnostics(logs) == [DIAGNOSTICS[identity]], 'scheduled_diagnostic_mismatch')
        require(github.fetch(path) == run, 'failed_run_changed_during_verification')
        result[identity] = {'failed_run': proof, 'failed_job': {'id': job['id'], 'steps_sha256': content_hash(steps)},
                            'log_sha256': sha(logs), 'diagnostic': DIAGNOSTICS[identity]}
    return result


def read_snapshot(github, identity, key):
    """Authenticate both actual hosted ZIPs, ciphertext and every inner member."""
    require(identity in (BEFORE, AFTER), 'source_snapshot_identity_invalid')
    before = identity == BEFORE
    expected_commit = BEFORE_COMMIT if before else PINNED_COMMIT
    expected_cipher = BEFORE_CIPHER_HASH if before else AFTER_CIPHER_HASH
    expected_state = BEFORE_FINGERPRINT if before else AFTER_FINGERPRINT
    expected_pages = BEFORE_PAGES if before else AFTER_PAGES
    run_path = '/actions/runs/' + identity
    run = github.fetch(run_path)
    require(str(run.get('id')) == identity and run.get('head_sha') == expected_commit
            and run.get('head_branch') == 'main' and type(run.get('run_attempt')) is int and run['run_attempt'] == 1
            and run.get('event') == 'workflow_dispatch' and run.get('status') == 'completed'
            and run.get('conclusion') == 'success'
            and run.get('display_title') == ('cloud:live:all' if before else 'history:snapshot')
            and str(run.get('path', '')).partition('@')[0] == '.github/workflows/' + (
                'cloud-operations.yml' if before else 'history-reconciliation.yml'), 'source_snapshot_hosted_run_invalid')
    jobs = github.fetch(run_path + '/jobs?filter=all&per_page=100&page=1')
    require(_complete_list(jobs, 'jobs', 1), 'source_snapshot_jobs_incomplete')
    job = jobs['jobs'][0]
    require(job.get('name') == ('orchestrate' if before else 'reconcile') and job.get('status') == 'completed'
            and job.get('conclusion') == 'success' and 'ubuntu-latest' in job.get('labels', [])
            and 'self-hosted' not in job.get('labels', []), 'source_snapshot_hosted_job_invalid')
    response = github.fetch(run_path + '/artifacts?per_page=100&page=1')
    require(_complete_list(response, 'artifacts', 1), 'source_snapshot_artifact_history_incomplete')
    artifact = response['artifacts'][0]
    require(artifact.get('name') == ('redacted-run-' if before else 'reconciliation-evidence-') + identity
            and artifact.get('expired') is False and type(artifact.get('id')) is int,
            'source_snapshot_artifact_invalid')
    zipped = github.download(artifact['id'])
    try:
        summary_name = 'summary.json' if before else 'maintenance-summary.json'
        cipher_name = 'cloud-backup-' + identity + '.aesgcm'
        with zipfile.ZipFile(io.BytesIO(zipped)) as archive:
            names = archive.namelist()
            require(len(names) == len(set(names)) == 2 and all(safe_name(name) for name in names)
                    and set(names) == {summary_name, cipher_name}
                    and all(item.file_size <= 64_000_000 for item in archive.infolist()),
                    'source_snapshot_artifact_members_invalid')
            summary = json.loads(archive.read(summary_name))
            ciphertext = archive.read(cipher_name)
        backup = summary.get('backup', {})
        require(backup.get('restore_verified') is True and backup.get('sha256') == sha(ciphertext) == expected_cipher
                and (summary.get('mode') == 'live' and summary.get('persistent') is True
                     if before else summary.get('operation') == 'encrypted_source_snapshot'),
                'source_snapshot_ciphertext_mismatch')
        with tempfile.TemporaryDirectory(prefix='apply-scheduled-reconciliation-') as folder:
            root = Path(folder)
            key_path, cipher_path = root / 'recovery.bin', root / 'snapshot.aesgcm'
            key_path.write_bytes(key)
            key_path.chmod(0o600)
            cipher_path.write_bytes(ciphertext)
            cipher_path.chmod(0o600)
            restored = root / 'restored'
            restored_proof = restore_archive(cipher_path, restored, key_path=key_path)
            require(restored_proof.get('hashes_verified') is True and restored_proof.get('restored_files') == 1
                    and {path.name for path in restored.iterdir()} == {'cloud-records.json'},
                    'source_snapshot_member_hashes_unverified')
            member = (restored / 'cloud-records.json').read_bytes()
            wrapper = json.loads(member)
        require(wrapper.get('schema') == 'apply-cloud-snapshot/v1', 'source_snapshot_wrapper_invalid')
        state = SnapshotState(wrapper['snapshot'])
        require(type(backup.get('records')) is int and backup['records'] == len(state.pages) == expected_pages
                and state.fingerprint() == expected_state, 'source_snapshot_content_mismatch')
    except ReconciliationBlocked:
        raise
    except Exception:
        raise ReconciliationBlocked('source_snapshot_authentication_failed') from None
    require(github.fetch(run_path) == run, 'source_snapshot_run_changed')
    return state, {'run_id': identity, 'artifact_id': artifact['id'], 'artifact_zip_sha256': sha(zipped),
                   'archive_sha256': sha(ciphertext), 'member_sha256': sha(member),
                   'state_sha256': state.fingerprint(), 'pages': len(state.pages)}


def _effects(state):
    result = {}
    for identity, row in state.rows.items():
        payload = row['payload']
        if (row['kind'] == 'outreach_events'
                or (row['kind'] == 'applications' and (payload.get('attempt') or payload.get('receipt')
                                                       or row['status'] == 'submitting'))
                or (row['kind'] == 'audit_events' and payload.get('status') in {'submitting', 'applied_verified'})
                or (row['kind'] == 'conversation_observations'
                    and (payload.get('sent_at') or payload.get('delivered_at')
                         or str(payload.get('delivery_state', '')).casefold() in {'sent', 'sent verified', 'delivered'}))):
            result[identity] = content_hash(core(row))
    return result


def verify_delta(before, after, exported):
    require(content_hash(exported) == BASELINE_HASH and len(exported.get('records', [])) == BASELINE_RECORDS,
            'authenticated_baseline_mismatch')
    require(before.data_source_id == after.data_source_id
            and source_metadata(before.snapshot['data_source'], data_source=True)
                == source_metadata(after.snapshot['data_source'], data_source=True), 'source_schema_changed')
    require(before.fingerprint() == BEFORE_FINGERPRINT and after.fingerprint() == AFTER_FINGERPRINT,
            'reviewed_source_fingerprint_mismatch')
    require(_time(before.snapshot['captured_at']) < _time(after.snapshot['captured_at']), 'source_snapshot_order_invalid')
    require(set(before.pages) <= set(after.pages)
            and all(before.pages[key] == after.pages[key] for key in before.pages)
            and set(before.rows) <= set(after.rows)
            and all(core(before.rows[key]) == core(after.rows[key]) for key in before.rows), 'prior_source_changed')
    additions = set(after.rows) - set(before.rows)
    require({key: content_hash(core(after.rows[key])) for key in additions} == ADDED_RECORD_CORE_HASHES
            and set(after.pages) - set(before.pages) == {after.page_ids[key] for key in additions},
            'source_delta_not_exact_reviewed_records')
    require(_effects(before) == _effects(after), 'new_attempt_receipt_or_outreach_effect')
    for state in (before, after):
        ready = state.get('deployment:cutover:v2')
        require(ready is not None and content_hash(core(ready)) == READY_CORE_HASH, 'ready_record_changed')
        validate_cutover_record(ready)
        report = validate_import(state, exported)
        require(report['records'] == report['unchanged'] == BASELINE_RECORDS
                and report['conflicted'] == report['blocked'] == 0, 'baseline_readback_changed')
    unfinished = {key.removesuffix(':started') for key, row in after.rows.items()
                  if row['kind'] == 'agent_runs' and key.endswith(':started')
                  and key.removesuffix(':started') + ':completed' not in after.rows}
    require(unfinished == {SLOT} and after.get(SLOT + ':completed') is None, 'unfinished_slot_scope_changed')
    started = after.get(SLOT + ':started')
    require(started is not None and started['status'] == 'started'
            and started['payload'].get('date') == '2026-10-18'
            and started['payload'].get('lane') == 'applications'
            and started['payload'].get('persistent') is True and started['payload'].get('mode') == 'live',
            'failed_slot_marker_mismatch')
    for lane in ('accepted', 'first_touch'):
        completed = after.get('run:2026-10-18:' + lane + ':completed')
        require(completed is not None and completed['kind'] == 'agent_runs' and completed['status'] == 'completed'
                and completed['payload'].get('summary', {}).get('sent') == 0, 'completed_lane_evidence_missing')
    return {'baseline_unchanged': BASELINE_RECORDS, 'historical_manifest_sha256': BASELINE_HASH,
            'ready_core_sha256': READY_CORE_HASH, 'prior_pages_unchanged': len(before.pages),
            'added_record_core_hashes': ADDED_RECORD_CORE_HASHES, 'new_attempts': 0, 'new_receipts': 0,
            'new_outreach_events': 0, 'failed_slot': SLOT}


def resolution_records(incidents, before_proof, after_proof, delta):
    shared = {'commit': PINNED_COMMIT, 'source_contract': SOURCE_HASHES, 'incidents': incidents,
              'before': before_proof, 'after': after_proof, 'delta': delta}
    rows = []
    for identity in FAILURES:
        no_submission = identity == FAILED
        payload = {'schema': SCHEMA, 'github_run_id': identity,
                   'reason': 'application_failed_before_submission' if no_submission else 'unfinished_slot_guard_before_business_execution',
                   'external_effects_reconciled': True, 'retry_external_effects': False, 'evidence': shared}
        rows.append(record('writer-resolution:' + identity, 'audit_events', payload,
                           'reconciled_no_submission' if no_submission else 'reconciled_no_business_execution'))
    slot = record(SLOT + ':reconciled', 'audit_events', {'schema': SLOT_SCHEMA, 'run_id': SLOT,
        'github_run_id': FAILED, 'external_effects_reconciled': True, 'retry_external_effects': False,
        'evidence': {'writer_resolution_id': rows[0]['id'], 'writer_resolution_sha256': rows[0]['hash'],
                     'after_state_sha256': AFTER_FINGERPRINT}}, 'reconciled_no_submission')
    # Publish the original writer proof before the slot reference. A partial
    # append remains fenced and exact replay can finish only missing records.
    return [*rows, slot]


def _resolution_valid(identity, marker):
    try:
        require_reviewed_pins()
        row = validate_record(marker)
        payload = row['payload']
        no_submission = identity == FAILED
        if not (identity in FAILURES and row['id'] == 'writer-resolution:' + identity
                and row['kind'] == 'audit_events'
                and row['status'] == ('reconciled_no_submission' if no_submission else 'reconciled_no_business_execution')
                and set(payload) == {'schema', 'github_run_id', 'reason', 'external_effects_reconciled', 'retry_external_effects', 'evidence'}
                and payload['schema'] == SCHEMA and payload['github_run_id'] == identity
                and payload['reason'] == ('application_failed_before_submission' if no_submission else 'unfinished_slot_guard_before_business_execution')
                and payload['external_effects_reconciled'] is True and payload['retry_external_effects'] is False):
            return False
        evidence = payload['evidence']
        if not (set(evidence) == {'commit', 'source_contract', 'incidents', 'before', 'after', 'delta'}
                and evidence['commit'] == PINNED_COMMIT and evidence['source_contract'] == SOURCE_HASHES
                and set(evidence['incidents']) == set(FAILURES)
                and evidence['before']['run_id'] == BEFORE and evidence['before']['archive_sha256'] == BEFORE_CIPHER_HASH
                and evidence['before']['state_sha256'] == BEFORE_FINGERPRINT and evidence['before']['pages'] == BEFORE_PAGES
                and evidence['after']['run_id'] == AFTER and evidence['after']['archive_sha256'] == AFTER_CIPHER_HASH
                and evidence['after']['state_sha256'] == AFTER_FINGERPRINT and evidence['after']['pages'] == AFTER_PAGES
                and evidence['delta'] == {'baseline_unchanged': BASELINE_RECORDS, 'historical_manifest_sha256': BASELINE_HASH,
                    'ready_core_sha256': READY_CORE_HASH, 'prior_pages_unchanged': BEFORE_PAGES,
                    'added_record_core_hashes': ADDED_RECORD_CORE_HASHES, 'new_attempts': 0, 'new_receipts': 0,
                    'new_outreach_events': 0, 'failed_slot': SLOT}):
            return False
        for failed_id, incident in evidence['incidents'].items():
            if not (set(incident) == {'failed_run', 'failed_job', 'log_sha256', 'diagnostic'}
                    and _run_proof_valid(failed_id, incident['failed_run'])
                    and set(incident['failed_job']) == {'id', 'steps_sha256'}
                    and type(incident['failed_job']['id']) is int and incident['failed_job']['id'] > 0
                    and _hash(incident['failed_job']['steps_sha256']) and _hash(incident['log_sha256'])
                    and incident['diagnostic'] == DIAGNOSTICS[failed_id]):
                return False
        return True
    except Exception:
        return False


def scheduled_resolution_matches(identity, marker, fetch, repository):
    """Recheck original run attempt and complete job/step proof on every wake."""
    try:
        if repository != REPOSITORY or not _resolution_valid(identity, marker):
            return False
        incident = marker['payload']['evidence']['incidents'][identity]
        expected = incident['failed_run']
        path = '/actions/runs/' + identity
        run = fetch(path)
        if not isinstance(run, dict) or any(run.get(key) != value for key, value in expected.items()):
            return False
        jobs = fetch(path + '/jobs?filter=all&per_page=100&page=1')
        if not _complete_list(jobs, 'jobs', 1):
            return False
        job = jobs['jobs'][0]
        if not (job.get('id') == incident['failed_job']['id'] and job.get('name') == 'orchestrate'
                and job.get('status') == 'completed' and job.get('conclusion') == 'failure'
                and 'ubuntu-latest' in job.get('labels', []) and 'self-hosted' not in job.get('labels', [])
                and isinstance(job.get('steps'), list)
                and content_hash(job['steps']) == incident['failed_job']['steps_sha256']):
            return False
        after = fetch(path)
        return isinstance(after, dict) and all(after.get(key) == value for key, value in expected.items())
    except Exception:
        return False


def slot_resolution_matches(identity, marker, writer_marker):
    try:
        row = validate_record(marker)
        return (identity == SLOT and _resolution_valid(FAILED, writer_marker)
                and row['id'] == SLOT + ':reconciled' and row['kind'] == 'audit_events'
                and row['status'] == 'reconciled_no_submission' and row['payload'] == {
                    'schema': SLOT_SCHEMA, 'run_id': SLOT, 'github_run_id': FAILED,
                    'external_effects_reconciled': True, 'retry_external_effects': False,
                    'evidence': {'writer_resolution_id': writer_marker['id'],
                                 'writer_resolution_sha256': writer_marker['hash'],
                                 'after_state_sha256': AFTER_FINGERPRINT}})
    except Exception:
        return False


def reconcile(store, exported, github, context, key):
    require(context.get('actions') is True and context.get('ref') == 'refs/heads/main'
            and context.get('repository') == REPOSITORY and context.get('actor') == 'narendranathe'
            and context.get('triggering_actor') == 'narendranathe' and context.get('event') == 'workflow_dispatch'
            and context.get('cloud_enabled') == 'false' and re.fullmatch('[0-9a-f]{40}', context.get('commit', ''))
            and isinstance(key, bytes) and len(key) == 32, 'owner_disabled_hosted_maintenance_required')
    require_reviewed_pins()
    github.verify_main(context['commit'])
    verify_maintenance_context(github, context)
    incidents = verify_incidents(github)
    before, before_proof = read_snapshot(github, BEFORE, key)
    after, after_proof = read_snapshot(github, AFTER, key)
    require(all(_time(before.snapshot['captured_at']) < _time(proof['failed_run']['run_started_at'])
                <= _time(proof['failed_run']['updated_at']) < _time(after.snapshot['captured_at'])
                for proof in incidents.values()), 'snapshot_does_not_bracket_failures')
    delta = verify_delta(before, after, exported)
    expected = resolution_records(incidents, before_proof, after_proof, delta)
    current = SnapshotState(store.snapshot_all())
    require(current.data_source_id == getattr(store, 'data_source_id', None) == after.data_source_id
            and _time(current.snapshot['captured_at']) >= _time(after.snapshot['captured_at']),
            'fresh_source_identity_or_time_mismatch')
    existing = set()
    for row in expected:
        old = current.get(row['id'])
        if old is not None:
            require(core(old) == core(row), 'existing_scheduled_resolution_conflict')
            existing.add(row['id'])
            current.pages.pop(current.page_ids[row['id']])
    require(current.fingerprint() == after.fingerprint(), 'fresh_source_changed_since_snapshot')
    github.verify_main(context['commit'])
    verify_maintenance_context(github, context)
    require(verify_incidents(github) == incidents, 'incident_evidence_changed_before_append')
    for row in expected:
        if row['id'] in existing:
            continue
        written = store.put(row)
        observed = store.get(row['id'])
        if (not isinstance(written, dict) or not isinstance(observed, dict)
                or core(written) != core(row) or core(observed) != core(row)):
            raise AmbiguousWriteError('Scheduled reconciliation readback uncertain; stop for reconciliation')
    return {'operation': 'pinned_scheduled_writer_reconciliation', 'verified': True,
            'unchanged': len(existing) == len(expected), 'appended': len(expected) - len(existing),
            'resolved_run_ids': list(FAILURES), 'resolved_slot': SLOT, 'external_effects': 0,
            'baseline_unchanged': BASELINE_RECORDS, 'cloud_enabled_changed': False, 'ready_record_changed': False,
            'resolution_sha256': {row['id']: row['hash'] for row in expected}}
