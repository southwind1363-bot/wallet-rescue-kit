#!/usr/bin/env python3
"""
ウォレット救出キット【案内つき・ローカル実行版】パターンA（スポンサー方式）

このスクリプトは、あなたのパソコンの中だけで動きます。
1つずつ質問が出るので、答えていくだけで救出が進みます。

仕組み（先に読んでください）:
- ガス代（手数料）は「安全なウォレット」が全部払います。
  被害ウォレットへのガス代補給は避けますが、漏洩鍵を持つ相手との競合は防げません。
- 被害ウォレット側の仕事は「署名」を作ることだけです。
- 秘密鍵は、このパソコンの中の入力欄（画面に表示されません）でだけ使います。
  ファイルに保存しません。ネットに送りません。
  プログラムが使い終わった鍵は、その都度手放すようにしています
  （ただし、パソコンの仕組み上、メモリから完全に消えたことまでは保証できません）。
- 処理の記録として、段階・取引ハッシュ・アドレスなどの情報（秘密鍵や署名は含みません）を、
  同じフォルダの rescue-run-state.json に保存します。
  中断したときに前回の状態を確認するために使うので、消さないでください。
- 秘密鍵やシードフレーズを、Webサイト・チャット・メール・他人に
  送ることは絶対にしないでください。送るように言う相手は詐欺です。

必要なもの:
- Python 3 と、ライブラリ web3 / eth-account
  （無ければ下の「準備」に出るコマンドを1回だけ実行してください）
- 被害ウォレットの秘密鍵（このパソコンで入力します）
- 安全なウォレット（別のシードフレーズで作った新しいウォレット推奨）の秘密鍵と、
  ガス代ぶんの POL（目安 0.1 POL 以上あると安心です）
- 被害ウォレットに残っている NFT の一覧
  （リストの貼り付け＋自動照合が標準。Alchemy の API キーでの自動取得も選べます）

対象チェーン: Polygon（チェーンID 137）
"""
import getpass
import json
import sys
import urllib.request
from pathlib import Path

try:
    from rescue_state import (FAILED, NOT_SENT, SUCCEEDED, UNKNOWN, Journal,
                              RescueHalt, RESUME, check_limits, submit_prepared)
except ImportError:
    print("部品ファイル rescue_state.py が同じフォルダにありません。")
    print("ZIPの中身を、全部同じフォルダに展開してから実行してください。")
    sys.exit(1)

JOURNAL = None

CHAIN_ID = 137
RPCS = [
    "https://polygon.gateway.tenderly.co",
    "https://polygon.drpc.org",
    "https://polygon-bor-rpc.publicnode.com",
    "https://1rpc.io/matic",
]
OFFICIAL_DELEGATOR = "0x63c0c19a282a1b52b07dd5a65b58948a07dae32b"
ZERO = "0x0000000000000000000000000000000000000000"

ABI_BATCH = [{"name": "approveAll", "type": "function", "stateMutability": "nonpayable",
              "inputs": [{"name": "nftContracts", "type": "address[]"}],
              "outputs": []}]
ABI721 = [{"name": "transferFrom", "type": "function", "stateMutability": "nonpayable",
           "inputs": [{"name": "from", "type": "address"}, {"name": "to", "type": "address"},
                      {"name": "tokenId", "type": "uint256"}], "outputs": []}]
ABI_CHECK = [
    {"name": "ownerOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "tokenId", "type": "uint256"}], "outputs": [{"type": "address"}]},
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "account", "type": "address"}, {"name": "id", "type": "uint256"}],
     "outputs": [{"type": "uint256"}]},
    {"name": "name", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"type": "string"}]},
]
ABI1155 = [
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "account", "type": "address"}, {"name": "id", "type": "uint256"}],
     "outputs": [{"type": "uint256"}]},
    {"name": "safeTransferFrom", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "from", "type": "address"}, {"name": "to", "type": "address"},
                {"name": "id", "type": "uint256"}, {"name": "amount", "type": "uint256"},
                {"name": "data", "type": "bytes"}], "outputs": []},
]
ABI_APPROVAL = [{"name": "isApprovedForAll", "type": "function", "stateMutability": "view",
                 "inputs": [{"name": "owner", "type": "address"},
                            {"name": "operator", "type": "address"}],
                 "outputs": [{"type": "bool"}]}]
ABI_BATCH_VIEW = [{"name": "beneficiary", "type": "function", "stateMutability": "view",
                   "inputs": [], "outputs": [{"type": "address"}]}]
# 旧版の救出用の道具（RescueBatch）が、チェーン上に置かれた後のコードの指紋。
# 再開時に「旧版の道具が付いたまま」かどうかを見分けるために使う。
OLD_BATCH_CODEHASH = "fcc663b411c989a84cc37400c415235a5db26ed6b4d54e2633e303f0f7609d76"
# 修正版の救出用の道具（RescueBatch）が、チェーン上に置かれた後のコードの形。
# 宛先（beneficiary）はコードの中に埋め込まれていて、その部分（3か所・各20バイト）
# だけが配置ごとに違う。そこで、宛先の部分を取り除いた形の指紋で
# 「コード自体が確認済みの修正版と同じか」を照合し、埋め込まれた宛先も別に取り出す。
# 下の値は、モックチェーンで実際に3回（宛先を変えて）置いたコード同士の差分から
# 割り出したもの（長さ1135バイト、宛先の位置は 128 / 164 / 389 バイト目から20バイト）。
BATCH_CODE_LENGTH = 1135
BATCH_BENEFICIARY_SLOTS = (128, 164, 389)
BATCH_MASKED_CODEHASH = "0f59bb21a2f8090ffd82fdd82c78973bb3bac2b54c7c43dfb5b14d00ad379b3e"


def say(text=""):
    print(text)


def ask(prompt):
    return input(prompt).strip()


def confirm(prompt):
    ans = ask(prompt + "（「はい」または「いいえ」を入力）: ")
    return ans.lower() in ("はい", "hai", "yes", "y", "ok", "おk", "うん")


def _read_hidden(prompt):
    """文字を隠して入力する。隠せない環境では、安全のため止める。"""
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        try:
            return getpass.getpass(prompt).strip().strip('"').strip("'")
        except Warning:
            stop("入力した文字を隠せない環境のため、安全のため止めました。"
                 "普通のパソコンの画面（黒い画面＝コマンドプロンプトなど）で実行してください。")


def ask_key(prompt):
    from eth_account import Account
    say("貼り付け方：先に秘密鍵をコピーしておき、この画面で右クリックすると貼り付きます。")
    say("入力中、文字は画面に表示されませんが、それが正常です（盗み見防止のため）。")
    say("貼れたら Enter を押してください。")
    say("⚠️ 鍵をコピーした後は、使い終わったら別の適当な文字をコピーし直して、")
    say("　 コピーした内容を上書きしておいてください。")
    say("　 （コピーしたままにすると、他のソフトに読まれることがあります）")
    while True:
        raw = _read_hidden(prompt)
        try:
            acct = Account.from_key(raw)
            return raw, acct
        except Exception:
            say("鍵の形になっていませんでした。コピーの範囲が長すぎたり、足りなかったりしたようです。")
            say("鍵だけ（64文字の英数字）を、1回だけ貼り直してください。")


def stop(message):
    say("")
    say("■ ここで止めました")
    say(message)
    err = RescueHalt(message)
    err.shown = True
    raise err


def make_w3():
    try:
        from web3 import Web3
        from web3.providers.rpc import HTTPProvider
    except ImportError:
        say("準備が足りません。先に次のコマンドを1回実行してください：")
        say("  python -m pip install web3 eth-account")
        sys.exit(1)

    class RotatingProvider(HTTPProvider):
        """接続先を複数持ち、1つが拒否（403等）されても自動で次に切り替える。
        ただし送信（eth_sendRawTransaction）だけは例外にする。応答が返らなくても
        実際には送信済みのことがあるため、自動で送り直さない。"""
        def __init__(self, uris):
            super().__init__(uris[0], exception_retry_configuration=None,
                             request_kwargs={
                "timeout": 20,
                "headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            })
            self._uris = list(uris)

        def make_request(self, method, params):
            if method == "eth_sendRawTransaction":
                return super().make_request(method, params)
            last = None
            for uri in self._uris:
                self.endpoint_uri = uri
                try:
                    return super().make_request(method, params)
                except Exception as e:
                    last = e
            raise last

    try:
        w3 = Web3(RotatingProvider(RPCS))
        if w3.is_connected():
            from web3.middleware import ExtraDataToPOAMiddleware
            w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            return w3
    except Exception as e:
        stop(f"チェーンに接続できませんでした（{e}）。ネット接続を確認して、もう一度実行してください。")
    stop("チェーンに接続できませんでした。ネット接続を確認して、もう一度実行してください。")


def check_wallet(w3, addr):
    raw = w3.eth.get_code(addr).hex().removeprefix("0x")
    bal = w3.eth.get_balance(addr) / 1e18
    nonce = w3.eth.get_transaction_count(addr)
    delegated_to = None
    if raw.startswith("ef0100"):
        delegated_to = "0x" + raw[6:46]
    return {"code": raw, "balance": bal, "nonce": nonce, "delegated_to": delegated_to}


def fetch_nfts_alchemy(victim, api_key):
    url = (f"https://polygon-mainnet.g.alchemy.com/nft/v3/{api_key}/getNFTsForOwner"
           f"?owner={victim}&withMetadata=false&pageSize=100")
    req = urllib.request.Request(url, headers={"accept": "application/json",
                                               "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    nfts = []
    for n in data.get("ownedNfts", []):
        typ = (n.get("tokenType") or "").upper()
        if typ not in ("ERC721", "ERC1155"):
            continue
        nfts.append({"contract": n["contract"]["address"],
                     "tokenId": int(n["tokenId"]), "type": typ})
    return nfts


def step0_intro():
    say("=" * 60)
    say("ウォレット救出キット【案内つき】パターンA：安全なウォレットが払う方法")
    say("=" * 60)
    say("この方法では、被害ウォレットにお金を送りません。")
    say("ただし、漏洩した鍵を持つ相手による操作との競合は防げません。")
    say("")
    say("進み方（全部で6ステップ）：")
    say("  1. 被害ウォレットの状態チェック（読むだけ・安全）")
    say("  2. 残っている NFT の一覧を作る")
    say("  3. 2つの鍵を確認してから「救出用の道具（RescueBatch）」を置く")
    say("  4. 被害ウォレットの署名を作る（このパソコンの中だけ）")
    say("  5. 救出の取引を2回送る（委任の付け替え→解除）")
    say("  6. NFT を安全なウォレットへ回収する")
    say("")
    say("ステップ3から、手数料とチェーン上の変更が発生します。")
    say("各処理は、結果（レシート）を確認できた場合だけ次へ進みます。")
    say("結果を確認できない場合は、その場で止まります（止まった記録が残ります）。")
    say("分からなくなったら、この画面を閉じて止めてください。")
    say("⚠️ ステップ5（署名と送信の段階）の途中で止めたまま放置すると、危ない状態が残ることがあります。")
    say("やむを得ず止めた場合は、時間をおかずに再実行してください。")
    say("再実行すると、最初に前回の記録とチェーンの状態を確認し、")
    say("状態が確かめられた場合だけ、続きを案内します。")
    if not confirm("内容が分かったら進みます（「いいえ」でここで終了）"):
        stop("問題ありません。準備ができたら、また実行してください。")


def step1_check(w3, expected_victim=None):
    say("")
    say("── ステップ1：被害ウォレットの状態チェック ──")
    say("アドレスを貼り付けてください（秘密鍵ではありません）。")
    from web3 import Web3
    raw = ask("被害ウォレットのアドレス: ")
    try:
        victim = Web3.to_checksum_address(raw)
    except Exception:
        stop("アドレスの形が正しくありません。0x から始まる42文字のアドレスを貼ってください。")
    if expected_victim and victim.lower() != expected_victim.lower():
        stop(f"入力されたアドレス（{victim}）が、前回の記録の被害ウォレット"
             f"（{expected_victim}）と違います。"
             "前回と同じ救出の続きとして確認が取れていないため、安全のため止めました。"
             "前回の被害ウォレットのアドレスを入力してください。")
    try:
        st = check_wallet(w3, victim)
    except Exception:
        stop("被害ウォレットの状態を確認できませんでした（未確認）。"
             "通信の失敗かもしれません。安全のため止めました。"
             "時間をおいて再実行してください。")
    say(f"アドレス: {victim}")
    say(f"POL残高: {st['balance']:.6f} / Nonce: {st['nonce']}")
    if st["delegated_to"] is None:
        say("委任は付いていません。")
        say("委任が無くても、秘密鍵が漏れた場合は、このキットで救出できます。")
        say("（途中でこのキットが委任を付け替えて、NFTの移転許可を取ります）")
        say("委任も無く、鍵漏れの心当たりも無い場合は、救出は不要です。")
        if not confirm("鍵漏れの心当たりがあるので、救出を続けますか"):
            stop("止めました。心配が無ければ、このウォレットは通常どおり使えます。")
        return victim, st
    say(f"委任が付いています → 委任先: {st['delegated_to']}")
    if st["delegated_to"].lower() == OFFICIAL_DELEGATOR:
        say("委任先は MetaMask 公式のスマートアカウント機能です。")
        say("心当たりがある（自分でオンにした）なら、侵害ではない可能性が高いです。")
        if not confirm("それでも救出を続けますか"):
            stop("止めました。MetaMask の「スマートアカウント」設定から自分でオフにできます。")
    elif _delegate_is_kit_tool(w3, st["delegated_to"]):
        say("委任先は、このキットの救出用の道具です。")
        say("前回の救出の途中で止まっている可能性があります。")
        say("このまま進めます（鍵を確認したあと、続きの状態かどうかを確かめます）。")
    else:
        say("⚠️ この委任先は、MetaMask公式のものでも、このキットの救出用の道具でもありません。")
        say("心当たりのない、第三者の委任が付いたままになっています。")
        say("このキットは、この委任をそのまま使うことはしません。")
        say("ステップ5で、委任をこのキットの道具に付け替えてから、最後に委任を外します。")
        if not confirm("この委任を付け替えて、救出を進めますか"):
            stop("止めました。委任は付いたままになっています。"
                 "付け替えてよいか分からない場合は、無理に進めないでください。")
    return victim, st


def verify_owned(w3, victim, row):
    """チェーンで「今も本人が持っているか」を確かめる。
    戻り値: (状態, 種類)。状態は "owned"（持っている）／"not_owned"（持っていない・確定）
    ／"unknown"（通信の失敗などで確認できなかった）のどれか。
    確認できなかった物を「持っていない」と扱わないために、状態を分けて返す。"""
    from web3 import Web3
    from web3.exceptions import BadFunctionCallOutput, ContractLogicError
    victim = Web3.to_checksum_address(victim)
    c = w3.eth.contract(address=Web3.to_checksum_address(row["contract"]), abi=ABI_CHECK)

    def as721():
        # True=持っている / False=持っていない（確定） / None=確認できなかった
        try:
            return c.functions.ownerOf(row["tokenId"]).call().lower() == victim.lower()
        except (ContractLogicError, BadFunctionCallOutput):
            return False
        except Exception:
            return None

    def as1155():
        try:
            return c.functions.balanceOf(victim, row["tokenId"]).call() > 0
        except (ContractLogicError, BadFunctionCallOutput):
            return False
        except Exception:
            return None

    if row["type"] == "ERC1155":
        r = as1155()
        if r is None:
            return "unknown", "ERC1155"
        return ("owned" if r else "not_owned"), "ERC1155"
    if row["type"] == "ERC721":
        r = as721()
        if r is None:
            return "unknown", "ERC721"
        return ("owned" if r else "not_owned"), "ERC721"
    r721 = as721()
    if r721:
        return "owned", "ERC721"
    r1155 = as1155()
    if r1155:
        return "owned", "ERC1155"
    if r721 is None or r1155 is None:
        return "unknown", "ERC721"
    return "not_owned", "ERC721"


def contract_name(w3, contract):
    from web3 import Web3
    try:
        c = w3.eth.contract(address=Web3.to_checksum_address(contract), abi=ABI_CHECK)
        return c.functions.name().call() or ""
    except Exception:
        return ""


def step2_nfts(w3, victim):
    say("")
    say("── ステップ2：残っている NFT の一覧 ──")
    say("救出できるのは「まだ被害ウォレットに残っている NFT」だけです。")
    say("すでに攻撃者のウォレットへ移された分は、取り戻せません。")
    say("")
    say("一覧の作り方は2つあります。")
    say("  1：リストを貼り付ける（キー不要）…ウェブページで作ったリストや、OpenSeaなどで調べたリストを貼ります")
    say("  2：Alchemy の API キーで自動取得する（キーは保存しません）")
    mode = ask("どちらの方法にしますか？（1 か 2）: ")
    rows = []
    if mode == "2":
        key = _read_hidden("Alchemy APIキー: ")
        if key:
            try:
                rows = fetch_nfts_alchemy(victim, key)
                say(f"取得できました：{len(rows)} 件")
            except Exception as e:
                say(f"自動取得に失敗しました（{str(e)[:60]}）。方法1（貼り付け）に切り替えます。")
                rows = []
            del key
        if not rows:
            say("方法1（貼り付け）に切り替えます。")
    if not rows:
        say("")
        say("NFT のリストを貼り付けてください。形式： コントラクトアドレス,TokenID,種類")
        say("種類は ERC721 か ERC1155。例： 0x1234...abcd,15,ERC721")
        say("何行でもまとめて貼って大丈夫です。貼り終わったら「おわり」と入力してください。")
        seen = set()
        while True:
            line = ask("").strip()
            if line in ("おわり", "終わり"):
                break
            if not line:
                continue
            if line.lower().startswith("contract,"):
                continue
            parts = [p.strip() for p in line.replace("、", ",").replace("，", ",").split(",")]
            try:
                from web3 import Web3
                c = Web3.to_checksum_address(parts[0])
                tid = int(parts[1], 0)
                typ = parts[2].upper() if len(parts) > 2 else ""
                if typ not in ("ERC721", "ERC1155"):
                    typ = ""
                k = (c.lower(), tid)
                if k in seen:
                    continue
                seen.add(k)
                rows.append({"contract": c, "tokenId": tid, "type": typ})
            except Exception:
                say(f"形式が違うため読み込めませんでした： {line[:50]}")
        say(f"読み込めました：{len(rows)} 件")
    if not rows:
        stop("NFT が0件です。残っている NFT が無い場合、救出するものがありません。")
    say("")
    say("チェーンで「今も本人が持っているか」を1件ずつ確かめます…")
    from collections import OrderedDict
    from web3 import Web3
    groups = OrderedDict()
    unknowns = []
    for i, r in enumerate(rows, 1):
        r["contract"] = Web3.to_checksum_address(r["contract"])
        state, typ = verify_owned(w3, victim, r)
        if state == "unknown":
            unknowns.append(r)
            continue
        g = groups.setdefault(r["contract"], {"owned": [], "dropped": 0})
        if state == "owned":
            r["type"] = typ
            g["owned"].append(r)
        else:
            g["dropped"] += 1
        if i % 20 == 0:
            say(f"  …{i} 件確認しました")
    if unknowns:
        say("")
        say(f"⚠️ 確認できなかった物：{len(unknowns)} 件")
        say("通信の失敗などで、「今も持っているか」が分からなかった物です。")
        say("持っていない物として、黙って外すことはしません。")
        for r in unknowns:
            say(f"  - {r['contract']} #{r['tokenId']}")
        say("確実なのは、時間をおいてスクリプトを再実行して、もう一度確かめることです。")
        say("再実行すると、最初に前回の記録とチェーンの状態を確認します。")
        if not confirm("確認できた物だけで、このまま進みますか"):
            stop("止めました。確認できなかった物は未確認のままです。"
                 "再実行すると、最初に状態確認を行います。")
    nfts = []
    say("")
    say("契約（コレクション）ごとに確認します。心当たりがある物だけを救出対象にします。")
    say("知らない契約は「いいえ」のままにしてください（勝手に送られた詐欺NFTの可能性があります）。")
    for contract, g in groups.items():
        if not g["owned"]:
            say(f"・{contract}：今は持っていない物だけでした（{g['dropped']} 件）ので、対象にしません。")
            continue
        name = contract_name(w3, contract)
        label = f"「{name}」" if name else "名前が分からない契約"
        say(f"・{label}（{contract}）：{len(g['owned'])} 件を持っています。")
        if g["dropped"]:
            say(f"　（持っていないため外した物：{g['dropped']} 件）")
        if confirm("この契約のNFTに心当たりがありますか（「いいえ」でこの候補を除外）"):
            nfts.extend(g["owned"])
            say(f"　→ 救出対象に入れました（今 {len(nfts)} 件）")
        else:
            say("　→ 対象にしませんでした。")
    if not nfts:
        stop("救出対象が0件です。心当たりのある NFT が無い場合、救出するものがありません。")
    say("")
    say(f"対象の NFT：{len(nfts)} 件")
    for n in nfts:
        say(f"  - {n['contract']} #{n['tokenId']}（{n['type']}）")
    if not confirm("この一覧で合っていますか"):
        stop("止めました。一覧を用意し直してから、また実行してください。")
    return nfts


def send_tx(w3, key, tx, stage):
    """送信は1回だけ。まず状態記録に送信予定を書き、送信前の確認
    （未確定取引の有無・接続先・ガス見積もり・上限・残高）を通らない場合は
    送信せずに止める。応答が返らない場合は「送信済み・結果未確認」として
    止め、未送信とは判断しない。"""
    index = JOURNAL.begin(stage)
    try:
        latest = w3.eth.get_transaction_count(tx["from"], "latest")
        pending = w3.eth.get_transaction_count(tx["from"], "pending")
        if latest != pending:
            raise RescueHalt("送信者に、結果が確定していない取引が残っています。"
                             "今回の試行は未送信です。" + RESUME)
        if w3.eth.chain_id != CHAIN_ID:
            raise RescueHalt("接続先のチェーンを確認できません。"
                             "今回の試行は未送信です。" + RESUME)
        tx["nonce"] = latest
        tx["chainId"] = CHAIN_ID
        tx.pop("gasPrice", None)
        gp = w3.eth.gas_price
        tx["maxPriorityFeePerGas"] = int(gp * 0.5)
        tx["maxFeePerGas"] = int(gp * 1.5)
        estimate = w3.eth.estimate_gas(tx)
        check_limits(estimate, tx["gas"], w3.eth.get_balance(tx["from"]),
                     tx["maxFeePerGas"], tx.get("value", 0))
        signed = w3.eth.account.sign_transaction(tx, key)
    except RescueHalt:
        raise
    except (Exception, KeyboardInterrupt):
        raise RescueHalt("送信前の準備・見積りを確認できません。"
                         "今回の試行は未送信です。" + RESUME) from None
    # 送信は自動で送り直さない。応答が無いことは、未送信の証明にはならない。
    return submit_prepared(w3.eth, JOURNAL, index, signed.raw_transaction,
                           signed.hash, tx["from"], tx["nonce"], CHAIN_ID)


def _batch_code_beneficiary(w3, code):
    """コード自体が「確認済みの修正版の道具」と同じ形なら、コードに埋め込まれた
    宛先（beneficiary）を返す。形が違う場合や、埋め込みがそろっていない場合は
    None を返す。宛先の読み取り値だけでは信用せず、コードの形で照合する。"""
    if len(code) != BATCH_CODE_LENGTH:
        return None
    values = [bytes(code[off:off + 20]) for off in BATCH_BENEFICIARY_SLOTS]
    if any(v != values[0] for v in values):
        return None
    masked = bytearray(code)
    for off in BATCH_BENEFICIARY_SLOTS:
        masked[off:off + 20] = b"\x00" * 20
    if w3.keccak(bytes(masked)).hex().removeprefix("0x") != BATCH_MASKED_CODEHASH:
        return None
    return "0x" + values[0].hex()


def _delegate_is_kit_tool(w3, delegated_to):
    """委任先が「このキットの救出用の道具」かどうかを、コードの形で見分ける。
    旧版・修正版のどちらの道具でも True、それ以外は False を返す。
    宛先（beneficiary）の読み取り値だけでは判断しない
    （道具のふりをする契約と見分けるため、コード自体で照合する）。
    コードが読み取れない場合は、その場で未確認として止める。"""
    from web3 import Web3
    target = Web3.to_checksum_address(delegated_to)
    try:
        code = w3.eth.get_code(target)
    except Exception:
        stop("委任先の状態を確認できませんでした（未確認）。"
             "通信の失敗かもしれません。安全のため止めました。"
             "時間をおいて再実行してください。")
    if w3.keccak(code).hex().removeprefix("0x") == OLD_BATCH_CODEHASH:
        return True
    return _batch_code_beneficiary(w3, code) is not None


def check_existing_delegate(w3, delegated_to, safe):
    """前から付いている委任が「このキットの救出の途中状態」かどうかを確かめる。
    「前回の続き」と認めるのは、委任先のコード自体が確認済みの修正版の道具と
    同じ形で、コードに埋め込まれた宛先も今回の安全なウォレットと一致する場合だけ。
    旧版の道具や、宛先が違う道具、形が確認できない道具は、引き継がずに止める。
    このキットの道具ではない委任先は、ステップ5で付け替えるので、そのまま進む。"""
    if delegated_to is None:
        return
    if delegated_to.lower() == OFFICIAL_DELEGATOR:
        return
    from web3 import Web3
    from web3.exceptions import BadFunctionCallOutput, ContractLogicError
    target = Web3.to_checksum_address(delegated_to)
    try:
        code = w3.eth.get_code(target)
    except Exception:
        stop("委任先の状態を確認できませんでした（通信の失敗かもしれません）。"
             "安全のため止めました。時間をおいて再実行してください。")
    if w3.keccak(code).hex().removeprefix("0x") == OLD_BATCH_CODEHASH:
        stop("被害ウォレットには、旧版の救出用の道具が付いたままになっています。"
             "旧版のまま中断した状態は、このキットでは引き継ぎません。"
             "安全のため止めました。配布元の案内を確認してください。")
    embedded = _batch_code_beneficiary(w3, code)
    if embedded is not None:
        if embedded.lower() == safe.lower():
            say("前回の救出の続きの状態でした（確認済みの道具で、宛先も同じ安全なウォレットでした）。")
            say("このまま進めます。")
            return
        stop("被害ウォレットには、別の安全なウォレット宛ての救出用の道具が付いたままになっています。"
             "今回の安全なウォレットとは宛先が違うため、このままでは進めません。"
             "安全のため止めました。前回使った安全なウォレットを用意するか、配布元の案内を確認してください。")
    try:
        c = w3.eth.contract(address=target, abi=ABI_BATCH_VIEW)
        c.functions.beneficiary().call()
    except (ContractLogicError, BadFunctionCallOutput):
        say("（委任先は、このキットの道具ではありません。救出の中で付け替えます）")
        return
    except Exception:
        stop("委任先が「このキットの道具かどうか」を確認できませんでした。"
             "安全のため止めました。時間をおいて再実行してください。")
    stop("委任先が、このキットで確認済みの道具かどうか分かりませんでした（未確認）。"
         "宛先の読み取り値だけでは「前回の続き」とは認められません。"
         "安全のため止めました。配布元の案内を確認してください。")


def _require_rescue_balances(qty, safe_before, victim_balance, safe_balance):
    """数量の照合（純粋な判定のみ）。案件開始時に記録した数量（qty＝被害側に
    あった数量、safe_before＝移動先に前からあった数量）と、現在の残高が
    一致しない場合や、値の形が確認できない場合は、進めてよい根拠には
    ならないとして止める。"""
    values = (qty, safe_before, victim_balance, safe_balance)
    if (any(type(v) is not int or v < 0 for v in values)
            or qty == 0 or victim_balance != qty or safe_balance != safe_before):
        raise RescueHalt("数量が記録と一致しないか、確認できません。送信には進みません。")


def _wallet_snapshot(state):
    """再開時の照合に使う、被害ウォレットの状態の要約を取り出す（純粋な整形のみ）。
    nonce・コード・委任先のいずれかが欠けている／形が違う場合は、状態を
    確認できていないものとして止める。"""
    if not isinstance(state, dict):
        raise RescueHalt("再開時の状態を確認できません。処理を停止します。")
    nonce, code, delegated = state.get("nonce"), state.get("code"), state.get("delegated_to")
    if (type(nonce) is not int or nonce < 0 or not isinstance(code, str)
            or "delegated_to" not in state
            or (delegated is not None and (not isinstance(delegated, str) or not delegated))):
        raise RescueHalt("再開時の状態を確認できません。処理を停止します。")
    return nonce, code.lower(), delegated.lower() if delegated is not None else None


def _require_same_wallet_state(expected, current):
    """再開の確認で得た状態と、直前に読み直した状態が同じかを照合する
    （純粋な判定のみ）。変わっていた状態を「新しい計画」として解釈し直す
    ことはせず、止める。"""
    if _wallet_snapshot(expected) != _wallet_snapshot(current):
        raise RescueHalt("再開確認後にnonceまたは委任の状態が変わりました。処理を停止します。")


def _check_remaining_quantities(w3, victim, safe, nfts):
    """送信の直前に、ERC1155 の数量が案件開始時の記録と一致するかを、
    読み取り専用で照合する。記録は書き換えず、取引も作らない。
    読み取れない場合も、記録と違う場合も、送信には進まず止める。"""
    from web3 import Web3
    for n in nfts:
        if n["type"] != "ERC1155":
            continue
        try:
            c = w3.eth.contract(address=Web3.to_checksum_address(n["contract"]),
                                abi=ABI1155)
            victim_balance = c.functions.balanceOf(victim, n["tokenId"]).call()
            safe_balance = c.functions.balanceOf(safe, n["tokenId"]).call()
        except Exception:
            raise RescueHalt("数量を読み取れません。送信には進みません。" + RESUME) from None
        _require_rescue_balances(n.get("qty"), n.get("safe_before"),
                                 victim_balance, safe_balance)


def _check_resume_wallet(w3, victim, expected):
    """再開の各段階で、被害ウォレットの状態を1回読み直し、ゲートで確認した
    状態と同じかまで照合して返す。読み取れない場合は、再配置などの次の
    経路を選ぶことはせず、その場で止める。"""
    try:
        current = check_wallet(w3, victim)
    except Exception:
        raise RescueHalt("再開時の状態を読み取れません。処理を停止します。" + RESUME) from None
    _require_same_wallet_state(expected, current)
    return current


def record_target_baselines(w3, victim, safe, nfts):
    """対象が確定した時点（最初の送信より前）の数量を、案件の記録に残す。
    ERC1155 は、被害ウォレットにある数量（qty）と、移動先に前からあった
    数量（safe_before）を分けて記録する。完了の確認では、被害ウォレットの
    数量が 0 で、移動先の数量が「前からあった分＋対象の数量」に一致する
    ことを両方確かめる（前からあった分を、今回の回収に数えないため）。
    読み取れない場合は、未確認として止める（まだ送信は行っていない）。"""
    from web3 import Web3
    changed = False
    for n in nfts:
        if n["type"] != "ERC1155":
            continue
        if n.get("qty") is not None and n.get("safe_before") is not None:
            continue
        c = w3.eth.contract(address=Web3.to_checksum_address(n["contract"]),
                            abi=ABI1155)
        try:
            n["qty"] = int(c.functions.balanceOf(victim, n["tokenId"]).call())
            n["safe_before"] = int(
                c.functions.balanceOf(safe, n["tokenId"]).call())
        except Exception:
            stop(f"対象の数量を確認できませんでした（未確認）："
                 f"{n['contract']} #{n['tokenId']}。"
                 "安全のため止めました。まだ送信は行っていません。"
                 "時間をおいて再実行してください。")
        changed = True
    if changed:
        JOURNAL.note_targets(nfts)


def step3_prepare_and_deploy(w3, victim, st, nfts):
    say("")
    say("── ステップ3：鍵の確認と、救出用の道具を置く ──")
    say("先に2つの鍵を確認します。鍵が合っていないと分かった時点で、")
    say("手数料がかかる作業に入る前に止まります。")
    say("")
    say("安全なウォレットは、被害ウォレットとは別のシードフレーズで作った")
    say("新しいウォレットを強くおすすめします（同じシードだと意味がありません）。")
    say("秘密鍵は画面に表示されません。このパソコンの中だけで使います。")
    safe_key, safe_acct = ask_key("安全なウォレットの秘密鍵: ")
    safe = safe_acct.address
    if safe.lower() == victim.lower():
        del safe_key
        stop("安全なウォレットと被害ウォレットが同じアドレスです。別のウォレットを用意してください。")
    say(f"安全なウォレット: {safe}")
    victim_key, vacct = ask_key("被害ウォレットの秘密鍵: ")
    if vacct.address.lower() != victim.lower():
        del safe_key, victim_key
        stop(f"鍵のアドレス（{vacct.address}）が、ステップ1の被害ウォレットと違います。"
             "鍵が一致しないため停止します。この実行ではまだ送信処理を呼んでいません。"
             "他の操作による状態変化まで否定するものではありません。")
    say("鍵を受け付けました（被害ウォレットの鍵で合っています）。")
    JOURNAL.note_safe(safe)
    record_target_baselines(w3, victim, safe, nfts)
    check_existing_delegate(w3, st["delegated_to"], safe)
    bal = w3.eth.get_balance(safe) / 1e18
    say(f"安全なウォレットの残高: {bal:.6f} POL（ガス代はここから払います）")
    if bal < 0.02:
        say("⚠️ 残高が少ないです。ガス代が足りないと途中で止まります。")
        if not confirm("それでも続けますか"):
            del safe_key, victim_key
            stop("止めました。安全なウォレットに POL を補充してから再実行してください。"
                 "再実行すると、最初に状態確認を行います。")
    say("")
    say("救出用の道具（RescueBatch という小さなプログラム）をチェーン上に置きます。")
    say("ガス代は安全なウォレットが払います。")
    if not confirm("置きますか"):
        del safe_key, victim_key
        stop("止めました。")
    bytecode = open("rescue_batch_bytecode.txt").read().strip()
    if not bytecode.startswith("0x"):
        bytecode = "0x" + bytecode
    _check_remaining_quantities(w3, victim, safe, nfts)
    txh, rcpt = send_tx(w3, safe_key, {"from": safe, "data": bytecode, "gas": 500000},
                        "deploy")
    batch = rcpt.contractAddress
    JOURNAL.note_batch(batch)
    say(f"置けました： {batch}")
    return safe, safe_key, victim_key, batch


def step4_sign(w3, victim, batch, victim_key):
    say("")
    say("── ステップ4：被害ウォレットの署名を作る ──")
    say("ステップ3で確認した被害ウォレットの鍵を、このパソコンの中だけで使います。")
    say("署名は2つ作ります：①委任を救出用の道具に付け替える ②委任を外す。")
    say("署名したあとは、他の操作をせずにすぐステップ5へ進んでください。")
    say("（間に被害ウォレットで何かすると、番号がずれて署名が無効になります）")
    from eth_account import Account
    nonce = w3.eth.get_transaction_count(victim)
    a1 = Account.sign_authorization(
        {"chainId": CHAIN_ID, "address": batch, "nonce": nonce}, victim_key)
    a2 = Account.sign_authorization(
        {"chainId": CHAIN_ID, "address": ZERO, "nonce": nonce + 1}, victim_key)
    del victim_key

    def dump(a):
        return {"chainId": a.chain_id, "address": a.address, "nonce": a.nonce,
                "yParity": a.y_parity, "r": a.r, "s": a.s}
    say("署名を作成しました。鍵や署名がメモリから完全に消去されたことは保証できません。")
    return dump(a1), dump(a2)


def step5_execute(w3, safe, safe_key, victim, batch, nfts, auth1, auth2):
    say("")
    say("── ステップ5：救出の取引を送る ──")
    contracts = sorted({n["contract"] for n in nfts})
    say("これから起きること：")
    say("  Tx1：被害ウォレットの委任を「救出用の道具」に付け替え、")
    say(f"        NFT {len(contracts)} コントラクト分について、安全なウォレットへの")
    say("        移転許可をまとめて出します。")
    say("  Tx2：被害ウォレットの委任を完全に外します。")
    say("ガス代は両方とも安全なウォレットが払います。被害ウォレットは0円です。")
    say("⚠️ Tx1 の結果を確認できない場合は、Tx2 を送らずに止まります。")
    if not confirm("実行しますか"):
        stop("止めました。画面を閉じただけでは、作った署名が無効になったとはいえません。"
             "再実行すると、最初に状態確認を行います。")
    from web3 import Web3
    c = w3.eth.contract(address=Web3.to_checksum_address(batch), abi=ABI_BATCH)
    call = c.functions.approveAll(contracts).build_transaction(
        {"from": safe, "gas": 300000})
    _check_remaining_quantities(w3, victim, safe, nfts)
    txh1, rcpt1 = send_tx(w3, safe_key, {
        "from": safe, "to": victim, "value": 0, "gas": 200000,
        "data": call["data"], "authorizationList": [auth1]}, "delegate_and_approve")
    say(f"Tx1 レシート成功確認： {txh1.hex()}")
    txh2, rcpt2 = send_tx(w3, safe_key, {
        "from": safe, "to": victim, "value": 0, "gas": 300000,
        "authorizationList": [auth2]}, "clear_delegation")
    say(f"Tx2 レシート成功確認： {txh2.hex()}")
    import time
    cleared = None
    for attempt in range(5):
        try:
            cleared = check_wallet(w3, victim)["delegated_to"] is None
        except Exception:
            cleared = None
        if cleared:
            break
        if attempt < 4:
            say("委任が外れたか、もう一度確かめます…")
            time.sleep(3)
    if cleared is None:
        stop("委任が外れたかどうか、確認できませんでした（未確認）。"
             "安全のため、回収には進みません。自動で再送はしません。"
             "再実行すると、最初に状態確認を行います。確認できない場合は進みません。")
    if not cleared:
        stop("委任がまだ残っているように見えます（未確認）。"
             "安全のため、回収には進みません。自動で再送はしません。"
             "再実行すると、最初に状態確認を行います。確認できない場合は進みません。")
    say("確認できました：被害ウォレットの委任は外れました。")
    verify_approvals(w3, victim, safe, contracts)


def approvals_status(w3, victim, safe, contracts):
    """移転許可の状態を、読み取り専用で確かめる。
    戻り値: (missing, unreadable)。
    missing は「許可が付いていないことを読み取れた」契約、
    unreadable は「読み取り自体が失敗して、許可の有無が分からない」契約。
    読み取れなかった場合を「許可なし」と取り違えないために分けて返す。"""
    from web3 import Web3
    missing, unreadable = [], []
    for caddr in contracts:
        try:
            c = w3.eth.contract(address=Web3.to_checksum_address(caddr), abi=ABI_APPROVAL)
            approved = c.functions.isApprovedForAll(victim, safe).call()
        except Exception:
            unreadable.append(caddr)
            continue
        if not approved:
            missing.append(caddr)
    return missing, unreadable


def verify_approvals(w3, victim, safe, contracts):
    """Tx1で出した移転許可が、本当に全ての契約ぶん付いているかを確かめる。
    救出用の道具の中では、許可の失敗が表に出ないことがあるため、ここで確かめ直す。
    付いていない場合と、読み取れなかった場合は、理由を分けて止める
    （読み取り不能を「許可なし」と判断して進むことはしない）。"""
    say("移転許可が全ての契約に付いているか、確かめます…")
    missing, unreadable = approvals_status(w3, victim, safe, contracts)
    if unreadable:
        stop(f"移転許可の状態を読み取れなかった契約：{len(unreadable)} 件"
             "（読み取り不能）。許可が付いていないとは判断できません。"
             "安全のため、回収には進みません。再送も再承認もしません。"
             "再実行すると、最初に状態確認を行い、許可の状態も確かめ直します。")
    if missing:
        stop(f"移転許可が付いていない契約：{len(missing)} 件。"
             "安全のため、回収には進みません。再送はしません。"
             "再実行すると、最初に状態確認を行い、許可の状態も確かめ直します。")
    say("確認できました：移転許可は全ての契約に付いています。")


def step6_recover(w3, safe, safe_key, victim, nfts):
    say("")
    say("── ステップ6：NFT を安全なウォレットへ回収する ──")
    if not confirm(f"{len(nfts)} 件の NFT を回収しますか"):
        stop("回収を止めました。移転許可や所有の状態は、その後変わる可能性があります。"
             "再開前に、状態を確認してください。")
    ok = 0
    for n in nfts:
        contract, token_id, typ = n["contract"], n["tokenId"], n["type"]
        try:
            if typ == "ERC1155":
                c = w3.eth.contract(address=contract, abi=ABI1155)
                bal = c.functions.balanceOf(victim, token_id).call()
                if bal == 0:
                    raise RescueHalt("対象の残高が0でした。以降の回収を停止します。"
                                     + RESUME)
                safe_balance = c.functions.balanceOf(safe, token_id).call()
                _require_rescue_balances(n.get("qty"), n.get("safe_before"),
                                         bal, safe_balance)
                n["amount"] = bal
                fn = c.functions.safeTransferFrom(victim, safe, token_id, bal, b"")
            else:
                c = w3.eth.contract(address=contract, abi=ABI721)
                n["amount"] = 1
                fn = c.functions.transferFrom(victim, safe, token_id)
            tx = fn.build_transaction({"from": safe, "chainId": CHAIN_ID})
            _check_remaining_quantities(w3, victim, safe, [n])
            txh, rcpt = send_tx(w3, safe_key, tx, f"recover:{contract}:{token_id}")
            ok += 1
            say(f"  レシート成功確認： {contract} #{token_id}")
        except RescueHalt:
            say(f"ここまでにレシートで成功を確認した回収：{ok} 件。"
                f"対象：{contract} #{token_id} で止まりました。")
            raise
        except (Exception, KeyboardInterrupt):
            raise RescueHalt(
                f"ここまでにレシートで成功を確認した回収：{ok} 件。"
                f"対象：{contract} #{token_id} の処理を確認できず、止まりました。"
                + RESUME) from None
    say("")
    say(f"選択した対象のうち、レシートで成功を確認した回収：{ok} 件")


def step7_final_check(w3, victim, safe, nfts):
    """最後に、対象のNFTが全部、安全なウォレットへ移ったかを、読み取り専用で
    確かめ直す。個々の取引の成功だけで救出全体を完了とはしない。
    ERC1155 は数量まで確かめる：被害ウォレットの数量が 0 で、移動先の数量が
    「案件開始時に記録した、前からあった分＋対象の数量」と一致する場合だけ
    移ったと認める。一部しか移っていない場合は完了とはしない。
    1件でも確認できない・移っていない場合は、完了とはせず止める。"""
    say("")
    say("最後に、対象のNFTが全部、安全なウォレットへ移ったか確かめます…")
    from web3 import Web3
    bad = []
    for n in nfts:
        contract, token_id, typ = n["contract"], n["tokenId"], n["type"]
        try:
            c = w3.eth.contract(address=Web3.to_checksum_address(contract),
                                abi=ABI_CHECK)
            if typ == "ERC1155":
                qty = n.get("qty")
                safe_before = n.get("safe_before")
                if qty is None or safe_before is None:
                    bad.append((contract, token_id))
                    continue
                bal_s = c.functions.balanceOf(safe, token_id).call()
                bal_v = c.functions.balanceOf(victim, token_id).call()
                if not (bal_v == 0 and bal_s == safe_before + qty):
                    bad.append((contract, token_id))
            else:
                if c.functions.ownerOf(token_id).call().lower() != safe.lower():
                    bad.append((contract, token_id))
        except Exception:
            bad.append((contract, token_id))
    if bad:
        stop(f"最後の確認で、安全なウォレットへの移転を確認できなかった対象："
             f"{len(bad)} 件。救出全体の完了とはしません（未確認）。"
             "状態記録を保管してください。再実行すると、最初に状態確認を行います。")
    say("確認できました：対象のNFTは全部、安全なウォレットに移っています。")


def _journal_phase(transactions, fallback_batch=None):
    """記録された工程結果から、想定されるチェーンの状態を導く。
    戻り値: (phase, active_batch)。phase は "pre_tx1"（Tx1の確定成功がまだ無い）／
    "tx1_done"（Tx1は確定成功し、Tx2はまだ）／"tx2_done"（Tx2まで確定成功）。
    「配置した道具（直近の配置の確定成功）」と「実際に委任が有効な道具
    （Tx1の確定成功で付け替えた先）」は分けて追う。Tx2の確定成功で、
    有効な委任は無くなる。Tx1の記録には、送信開始時に記録した
    delegation_target（付け替え予定の先）があり、それが直近の配置と
    一致する場合だけ、有効な委任として認める。古い記録
    （delegation_target が無い）では、直前の配置の結果から付け替え先を導く。
    トップレベルの「直近の道具」の記録だけでは過去の有効な委任は特定
    できないため、判定には使わない（fallback_batch は呼び出し互換のため
    受け取るだけ）。
    記録の並びや値が想定と違う場合は (None, None) を返し、呼び出し側は
    「不一致」として止める。"""
    latest_deployed = None
    active_batch = None
    phase = "pre_tx1"
    for rec in transactions:
        stage, state = rec.get("stage"), rec.get("state")
        if state not in (NOT_SENT, FAILED, SUCCEEDED):
            return None, None
        if stage == "deploy":
            if state == SUCCEEDED:
                address = rec.get("contract_address")
                if not isinstance(address, str) or not address:
                    return None, None
                latest_deployed = address
            continue
        if stage == "delegate_and_approve":
            if state == SUCCEEDED:
                target = rec.get("delegation_target", latest_deployed)
                if (not isinstance(target, str) or not target
                        or latest_deployed is None
                        or target.lower() != latest_deployed.lower()):
                    return None, None
                active_batch = target
                phase = "tx1_done"
            continue
        if stage == "clear_delegation":
            if state == SUCCEEDED:
                if phase != "tx1_done":
                    return None, None
                active_batch = None
                phase = "tx2_done"
            continue
        if isinstance(stage, str) and stage.startswith("recover:"):
            if phase != "tx2_done":
                return None, None
            continue
        return None, None
    return phase, active_batch


def locate_nft(w3, victim, safe, row):
    """対象のNFTが今どこにあるかを、読み取り専用で確かめる。
    戻り値: (場所, 数量)。場所は "victim"（被害ウォレットにある）／
    "safe"（移動先にある）／"elsewhere"（どちらでもない）／
    "partial"（ERC1155 で、一部だけが移っているなど、記録の数量と
    合わない状態）／"unknown"（通信の失敗などで確認できなかった）。
    ERC721 は ownerOf、ERC1155 は残高で判定する。ERC1155 は、案件開始時に
    記録した数量（qty＝被害ウォレットにあった数量、safe_before＝移動先に
    前からあった数量）と一致するかまで確かめる。前からあった分だけが
    移動先にある状態を「回収済み」とは数えない。数量の記録が無い対象は、
    量まで照合できないため "unknown" として扱う。"""
    from web3 import Web3
    from web3.exceptions import BadFunctionCallOutput, ContractLogicError
    c = w3.eth.contract(address=Web3.to_checksum_address(row["contract"]),
                        abi=ABI_CHECK)
    if row["type"] == "ERC1155":
        try:
            bal_v = c.functions.balanceOf(victim, row["tokenId"]).call()
            bal_s = c.functions.balanceOf(safe, row["tokenId"]).call()
        except Exception:
            return "unknown", 0
        qty = row.get("qty")
        safe_before = row.get("safe_before")
        if qty is None or safe_before is None:
            return "unknown", 0
        if bal_v == 0 and bal_s == safe_before + qty:
            return "safe", qty
        if bal_v == qty and bal_s == safe_before:
            return "victim", qty
        return "partial", bal_v
    try:
        owner = c.functions.ownerOf(row["tokenId"]).call()
    except (ContractLogicError, BadFunctionCallOutput):
        return "elsewhere", 0
    except Exception:
        return "unknown", 0
    if owner.lower() == victim.lower():
        return "victim", 1
    if owner.lower() == safe.lower():
        return "safe", 1
    return "elsewhere", 0


def gate_confirm_resume(w3, journal):
    """再開の入口。読み取り専用で、前回の記録が「同じ案件の続き」として
    チェーンの状態と本当に一致するか、次の全てを照合する。
    ここでは署名も送信もせず、秘密鍵も聞かない。
      a. 入力された被害ウォレットが、記録と同一か
      b. 記録された工程結果と、現在の委任の状態が矛盾しないか
      c. 対象のNFTのうち回収済みの物は記録の移動先にあるか、
         未回収の物は被害ウォレットに残っているか
      d. 続きの移動先は、記録の移動先に固定される（差し替えできない）
    1つでも合わない・確認できない場合は「未確認／不一致」として止める
    （再送も新規開始も案内しない）。全て一致し、利用者が同意した場合だけ、
    続きの計画（対象と移動先を引き継いだもの）を返す。"""
    from web3 import Web3
    data = journal.data
    victim_rec = data.get("victim")
    safe_rec = data.get("safe")
    targets = data.get("targets")
    valid_targets = (
        isinstance(targets, list) and len(targets) > 0 and all(
            isinstance(t, dict)
            and isinstance(t.get("contract"), str)
            and isinstance(t.get("tokenId"), int)
            and t.get("type") in ("ERC721", "ERC1155")
            and (t.get("type") != "ERC1155"
                 or (isinstance(t.get("qty"), int)
                     and isinstance(t.get("safe_before"), int)))
            for t in targets))
    if not victim_rec or not safe_rec or not valid_targets:
        stop("前回の記録に、案件を確認するための情報（被害ウォレット・移動先・"
             "対象の一覧）がそろっていません（未確認）。"
             "記録と状態が同じ案件として確認できないため、安全のため止めました。"
             "再送も新規開始もしません。" + RESUME)
    normalized = []
    for t in targets:
        nt = {"contract": Web3.to_checksum_address(t["contract"]),
              "tokenId": t["tokenId"], "type": t["type"]}
        if t["type"] == "ERC1155":
            nt["qty"] = t["qty"]
            nt["safe_before"] = t["safe_before"]
        normalized.append(nt)
    targets = normalized
    say("")
    say("前回の記録の内容：")
    say(f"  被害ウォレット: {victim_rec}")
    say(f"  移動先（安全なウォレット）: {safe_rec}")
    say(f"  救出対象: {len(targets)} 件")
    for line in journal.progress_lines():
        say(line)
    # --- a. 被害ウォレットの同一性 ---
    say("")
    say("確認のため、前回の被害ウォレットのアドレスを貼り付けてください"
        "（秘密鍵ではありません）。")
    raw = ask("被害ウォレットのアドレス: ")
    try:
        victim_in = Web3.to_checksum_address(raw)
    except Exception:
        stop("入力されたアドレスの形が確認できませんでした（未確認）。"
             "前回の記録と一致するか確かめられないため、安全のため止めました。"
             "再送も新規開始もしません。" + RESUME)
    if victim_in.lower() != victim_rec.lower():
        stop(f"入力されたアドレス（{victim_in}）が、前回の記録の被害ウォレット"
             f"（{victim_rec}）と一致しません。"
             "前回と別の案件の可能性があるため、安全のため止めました。"
             "再送も新規開始もしません。" + RESUME)
    victim = victim_rec
    # --- b. 工程結果と委任状態の照合 ---
    phase, batch = _journal_phase(data["transactions"], data.get("batch"))
    if phase is None:
        stop("前回の記録の工程の並びが、想定と一致しません（不一致）。"
             "安全のため止めました。再送も新規開始もしません。" + RESUME)
    try:
        st = check_wallet(w3, victim)
    except Exception:
        stop("前回の被害ウォレットの状態を確認できませんでした（未確認）。"
             "安全のため止めました。再送も新規開始もしません。" + RESUME)
    _wallet_snapshot(st)
    delegated = st["delegated_to"]
    if delegated is not None:
        try:
            code = w3.eth.get_code(Web3.to_checksum_address(delegated))
        except Exception:
            stop("委任先の状態を確認できませんでした（未確認）。"
                 "安全のため止めました。再送も新規開始もしません。" + RESUME)
        if w3.keccak(code).hex().removeprefix("0x") == OLD_BATCH_CODEHASH:
            stop("被害ウォレットには、旧版の救出用の道具が付いたままになっています。"
                 "旧版のまま中断した状態は、このキットでは引き継ぎません。"
                 "安全のため止めました。配布元の案内を確認してください。")
    if phase == "tx2_done":
        if delegated is not None:
            stop("前回の記録では、委任を外す取引（Tx2）が確定成功していますが、"
                 "現在は委任が付いたままです（記録と状態が一致しません）。"
                 "安全のため止めました。再送も新規開始もしません。" + RESUME)
    elif phase == "tx1_done":
        if delegated is None:
            stop("前回の記録では、委任を付け替える取引（Tx1）が確定成功していますが、"
                 "現在は委任が付いていません（記録と状態が一致しません）。"
                 "安全のため止めました。再送も新規開始もしません。" + RESUME)
        if batch is None:
            stop("前回、チェーン上に置いた救出用の道具の記録が確認できません"
                 "（未確認）。委任先が前回と同じ道具か確かめられないため、"
                 "安全のため止めました。再送も新規開始もしません。" + RESUME)
        if delegated.lower() != batch.lower():
            stop("現在の委任先が、前回の記録の救出用の道具と一致しません"
                 "（記録と状態が一致しません）。"
                 "安全のため止めました。再送も新規開始もしません。" + RESUME)
        embedded = _batch_code_beneficiary(w3, code)
        if embedded is None:
            stop("委任先が、前回と同じ救出用の道具か確認できませんでした"
                 "（未確認）。安全のため止めました。"
                 "再送も新規開始もしません。" + RESUME)
        if embedded.lower() != safe_rec.lower():
            stop("委任先の道具の宛先が、前回の記録の移動先と一致しません"
                 "（記録と状態が一致しません）。"
                 "安全のため止めました。再送も新規開始もしません。" + RESUME)
    else:  # pre_tx1：Tx1の確定成功が無いのに、前回の道具が効いていてはいけない
        if delegated is not None and delegated.lower() != OFFICIAL_DELEGATOR:
            embedded = _batch_code_beneficiary(w3, code)
            if embedded is not None:
                stop("前回の記録には、委任を付け替える取引（Tx1）の確定成功が"
                     "ありませんが、現在は救出用の道具への委任が付いています"
                     "（記録と状態が一致しません）。"
                     "安全のため止めました。再送も新規開始もしません。" + RESUME)
            from web3.exceptions import (BadFunctionCallOutput,
                                          ContractLogicError)
            try:
                c = w3.eth.contract(address=Web3.to_checksum_address(delegated),
                                    abi=ABI_BATCH_VIEW)
                c.functions.beneficiary().call()
            except (ContractLogicError, BadFunctionCallOutput):
                pass  # 第三者の委任。続きの中で付け替える
            except Exception:
                stop("委任先が何かを確認できませんでした（未確認）。"
                     "安全のため止めました。再送も新規開始もしません。" + RESUME)
            else:
                stop("委任先が、このキットで確認済みの道具かどうか分かりません"
                     "でした（未確認）。宛先の読み取り値だけでは「前回の続き」とは"
                     "認められません。安全のため止めました。"
                     "配布元の案内を確認してください。")
    # --- c. 対象NFTの照合 ---
    recovered_stages = {
        rec.get("stage") for rec in data["transactions"]
        if rec.get("state") == SUCCEEDED
        and isinstance(rec.get("stage"), str)
        and rec.get("stage").startswith("recover:")}
    remaining = []
    for t in targets:
        stage = f"recover:{t['contract']}:{t['tokenId']}"
        done = stage in recovered_stages
        where, amount = locate_nft(w3, victim, safe_rec, t)
        if where == "unknown":
            stop(f"対象の状態を確認できませんでした（未確認）："
                 f"{t['contract']} #{t['tokenId']}。"
                 "安全のため止めました。再送も新規開始もしません。" + RESUME)
        if where == "partial":
            stop(f"対象の一部だけが移っているなど、数量が前回の記録と"
                 f"一致しません：{t['contract']} #{t['tokenId']}"
                 "（記録と状態が一致しません）。"
                 "安全のため止めました。再送も新規開始もしません。" + RESUME)
        if done:
            if where != "safe":
                stop(f"前回の記録では回収が確定成功していますが、対象が記録の"
                     f"移動先にありません：{t['contract']} #{t['tokenId']}"
                     "（記録と状態が一致しません）。"
                     "安全のため止めました。再送も新規開始もしません。" + RESUME)
        else:
            if where != "victim":
                stop(f"対象が、被害ウォレットにも記録の移動先にもありません："
                     f"{t['contract']} #{t['tokenId']}"
                     "（記録と状態が一致しません）。"
                     "安全のため止めました。再送も新規開始もしません。" + RESUME)
            t = dict(t)
            t["amount"] = amount
            remaining.append(t)
    # --- d. 移動先の固定と、残りの提示 ---
    say("")
    say("記録された工程の結果と、現在のチェーンの状態が一致しました。")
    say(f"  被害ウォレット: {victim}")
    say(f"  移動先: {safe_rec}（前回の記録のものに固定されます。"
        "別の移動先には変更できません）")
    if not remaining:
        say("  対象は全て、前回の記録の移動先へ移っていることを確認しました。")
        if not confirm("この状態から、案内に沿って続けますか"):
            stop("止めました。前回の記録はそのまま残っています。"
                 "再送も新規開始もしていません。")
        return {"kind": "already_complete", "victim": victim,
                "safe": safe_rec, "targets": targets, "remaining": [],
                "phase": phase, "wallet_state": dict(st)}
    say(f"  残り: {len(remaining)} 件（回収済みの物は、もう一度移すことはしません）")
    for t in remaining:
        say(f"    - {t['contract']} #{t['tokenId']}（{t['type']}）")
    if not confirm("この状態から、案内に沿って続けますか"):
        stop("止めました。前回の記録はそのまま残っています。"
             "再送も新規開始もしていません。")
    return {"kind": "continue", "victim": victim, "safe": safe_rec,
            "targets": targets, "remaining": remaining, "phase": phase,
            "wallet_state": dict(st)}


def step6_recover_remaining(w3, safe, safe_key, victim, remaining):
    """再開の続き専用。各アイテムを実行直前にチェーンで確かめ直し、
    すでに移動先にある物は飛ばし、被害ウォレットに残っている物だけを、
    記録の移動先へ移す。どちらでもない場所にある・確認できない場合は止める。"""
    to_move = []
    for t in remaining:
        where, amount = locate_nft(w3, victim, safe, t)
        if where == "safe":
            say(f"  すでに移動先にあります（飛ばします）： "
                f"{t['contract']} #{t['tokenId']}")
            continue
        if where == "partial":
            raise RescueHalt(
                f"対象の一部だけが移っているなど、数量が実行直前の確認と"
                f"一致しません：{t['contract']} #{t['tokenId']}。"
                "安全のため、回収を止めました。" + RESUME)
        if where != "victim":
            raise RescueHalt(
                f"対象の現在の場所が、実行直前の確認と一致しません："
                f"{t['contract']} #{t['tokenId']}。"
                "安全のため、回収を止めました。" + RESUME)
        t = dict(t)
        t["amount"] = amount
        to_move.append(t)
    if not to_move:
        say("回収する残りはありませんでした。")
        return
    step6_recover(w3, safe, safe_key, victim, to_move)


def resume_after_gate(w3, plan):
    """ゲート通過後の続き。対象と移動先は前回の記録から引き継ぎ、
    入れ直しはさせない。鍵は、記録の移動先・被害ウォレットと一致する
    場合だけ受け付ける。"""
    victim = plan["victim"]
    safe_rec = plan["safe"]
    remaining = plan["remaining"]
    step0_intro()
    say("")
    say("── 前回の続き：鍵の確認 ──")
    say("移動先は、前回の記録の安全なウォレットに固定されています。")
    say("別の移動先の鍵を入れても、続きは実行できません。")
    safe_key, safe_acct = ask_key("安全なウォレットの秘密鍵: ")
    safe = safe_acct.address
    if safe.lower() != safe_rec.lower():
        del safe_key
        stop(f"入力された鍵のアドレス（{safe}）が、前回の記録の移動先"
             f"（{safe_rec}）と一致しません。"
             "続きの移動先は、前回の記録のものに固定されます。"
             "別の移動先では続けられないため、安全のため止めました。")
    say(f"移動先: {safe}（前回の記録と一致しました）")
    victim_key, vacct = ask_key("被害ウォレットの秘密鍵: ")
    if vacct.address.lower() != victim.lower():
        del safe_key, victim_key
        stop(f"鍵のアドレス（{vacct.address}）が、前回の記録の被害ウォレット"
             "と一致しません。鍵が一致しないため停止します。"
             "この実行ではまだ送信処理を呼んでいません。")
    say("鍵を受け付けました（被害ウォレットの鍵で合っています）。")
    bal = w3.eth.get_balance(safe) / 1e18
    say(f"安全なウォレットの残高: {bal:.6f} POL（ガス代はここから払います）")
    if bal < 0.02:
        say("⚠️ 残高が少ないです。ガス代が足りないと途中で止まります。")
        if not confirm("それでも続けますか"):
            del safe_key, victim_key
            stop("止めました。安全なウォレットに POL を補充してから再実行してください。"
                 "再実行すると、最初に状態確認を行います。")
    # 続きに入る前に、ゲートで確認した状態と今の状態が同じかと、残りの
    # 数量が記録と一致するかを、読み取り専用で照合し直す。読めない場合や
    # 変わっていた場合は、経路を選んだり解釈し直したりせず、ここで止める。
    # Tx2まで終わっていて、残りの移転許可も残っている場合は、
    # 委任まわりの処理をやり直さず、回収だけを続ける。
    contracts = sorted({t["contract"] for t in remaining})
    st_now = _check_resume_wallet(w3, victim, plan.get("wallet_state"))
    _check_remaining_quantities(w3, victim, safe, remaining)
    skip_machinery = False
    if plan["phase"] == "tx2_done":
        if st_now["delegated_to"] is not None:
            stop("前回の記録では、委任を外す取引（Tx2）が確定成功していますが、"
                 "現在は委任が付いたままです（記録と状態が一致しません）。"
                 "安全のため止めました。再送も新規開始もしません。" + RESUME)
        missing, unreadable = approvals_status(w3, victim, safe, contracts)
        if unreadable:
            del safe_key, victim_key
            stop("移転許可の状態を読み取れませんでした（読み取り不能）。"
                 "許可が付いていないとは判断できないため、道具の再配置や"
                 "再承認には進みません。安全のため止めました。"
                 "再実行すると、最初に状態確認を行います。")
        if not missing:
            skip_machinery = True
    if not skip_machinery:
        st_now = _check_resume_wallet(w3, victim, plan.get("wallet_state"))
        check_existing_delegate(w3, st_now["delegated_to"], safe)
        say("")
        say("救出用の道具（RescueBatch という小さなプログラム）をチェーン上に置きます。")
        say("ガス代は安全なウォレットが払います。")
        if not confirm("置きますか"):
            del safe_key, victim_key
            stop("止めました。")
        bytecode = open("rescue_batch_bytecode.txt").read().strip()
        if not bytecode.startswith("0x"):
            bytecode = "0x" + bytecode
        _check_resume_wallet(w3, victim, plan.get("wallet_state"))
        _check_remaining_quantities(w3, victim, safe, remaining)
        txh, rcpt = send_tx(w3, safe_key,
                            {"from": safe, "data": bytecode, "gas": 500000},
                            "deploy")
        batch = rcpt.contractAddress
        JOURNAL.note_batch(batch)
        say(f"置けました： {batch}")
        _check_resume_wallet(w3, victim, plan.get("wallet_state"))
        _check_remaining_quantities(w3, victim, safe, remaining)
        auth1, auth2 = step4_sign(w3, victim, batch, victim_key)
        step5_execute(w3, safe, safe_key, victim, batch, remaining,
                      auth1, auth2)
    else:
        del victim_key
        say("委任は外れていて、移転許可も残っていることを確認しました。")
        say("回収だけを続けます。")
    if skip_machinery:
        _check_resume_wallet(w3, victim, plan.get("wallet_state"))
    step6_recover_remaining(w3, safe, safe_key, victim, remaining)
    step7_final_check(w3, victim, safe, plan["targets"])
    del safe_key
    JOURNAL.complete()


def _finish_messages():
    say("")
    say("=" * 60)
    say("選択した対象の処理が、全て確認できました。")
    say("状態記録（rescue-run-state.json）は、確認のために保管してください。")
    say("最後に大切なこと：")
    say("- 被害ウォレットは、もう使わないでください（鍵が漏れた前提です）。")
    say("- 今後は安全なウォレットを使ってください。")
    say("- 秘密鍵やシードフレーズは、誰にも・どこにも送らないでください。")
    say("=" * 60)


def main():
    global JOURNAL
    JOURNAL = Journal(Path(__file__).with_name("rescue-run-state.json"))
    mode = JOURNAL.prepare()
    if mode == "needs_check":
        say("前回の救出の途中記録が残っています。")
        say("まず、記録とチェーンの状態を確認します。")
        say("（この確認では、署名も送信もせず、秘密鍵も聞きません。読むだけです）")
        w3 = make_w3()
        left = JOURNAL.resolve_unknowns(w3.eth)
        if left:
            hashes = "、".join(t["tx_hash"] for t in left)
            stop("前回の送信のうち、結果を確認できないものがあります（未確認）。"
                 f"Tx: {hashes}。"
                 "「取引が見つからない」だけでは、未送信とは判断しません。"
                 "安全のため、ここで止めました。再送も新規開始もしません。" + RESUME)
        plan = gate_confirm_resume(w3, JOURNAL)
        if plan["kind"] == "already_complete":
            # 対象は全て移動先にあり、最終状態まで確認できたので、
            # 記録を完了にして終える（送信はしない）。
            JOURNAL.complete()
            say("前回の対象は全て、安全なウォレットへ移っていることを確認しました。")
            _finish_messages()
            return
        resume_after_gate(w3, plan)
        _finish_messages()
        return
    if mode == "completed":
        say("前回の救出は、完了した記録がありました。")
        say("記録は rescue-run-state.previous.json に移しました。新しい救出を始めます。")
    elif mode == "not_sent":
        say("前回の実行は、送信を始める前に止まっていました。")
        say("記録は rescue-run-state.previous.json に移しました。新しい実行を始めます。")
    w3 = make_w3()
    step0_intro()
    victim, st = step1_check(w3)
    JOURNAL.note_victim(victim)
    nfts = step2_nfts(w3, victim)
    JOURNAL.note_targets(nfts)
    safe, safe_key, victim_key, batch = step3_prepare_and_deploy(
        w3, victim, st, nfts)
    auth1, auth2 = step4_sign(w3, victim, batch, victim_key)
    step5_execute(w3, safe, safe_key, victim, batch, nfts, auth1, auth2)
    step6_recover(w3, safe, safe_key, victim, nfts)
    step7_final_check(w3, victim, safe, nfts)
    del safe_key
    JOURNAL.complete()
    _finish_messages()


if __name__ == "__main__":
    try:
        main()
    except RescueHalt as e:
        if not getattr(e, "shown", False):
            say("")
            say("■ ここで止めました")
            say(str(e))
        say("処理は停止しました。全て成功したとは限りません。")
        say("状態記録（rescue-run-state.json）と、表示された取引ハッシュを保管してください。")
        sys.exit(1)
    except KeyboardInterrupt:
        say("")
        say("■ 中断しました")
        say("送信の結果が確認できていない場合があります。")
        say("状態記録（rescue-run-state.json）と、表示された取引ハッシュを保管してください。")
        sys.exit(1)
    except Exception as e:
        say("")
        say("■ ここで止めました")
        say(f"処理中に確認できないエラーが発生しました（{type(e).__name__}）。")
        say("全て成功したとは限りません。再実行すると、最初に状態確認を行います。")
        say("状態記録（rescue-run-state.json）と、表示された取引ハッシュを保管してください。")
        sys.exit(1)
