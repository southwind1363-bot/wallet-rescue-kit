#!/usr/bin/env python3
"""
ウォレット救出キット【案内つき・ローカル実行版】パターンA（スポンサー方式）

このスクリプトは、あなたのパソコンの中だけで動きます。
1つずつ質問が出るので、答えていくだけで救出が進みます。

仕組み（先に読んでください）:
- ガス代（手数料）は「安全なウォレット」が全部払います。
  被害ウォレットにはお金を送らないので、攻撃者との取り合いになりません。
- 被害ウォレット側の仕事は「署名」を作ることだけです。
- 秘密鍵は、このパソコンの中の入力欄（画面に表示されません）でだけ使います。
  ファイルに保存しません。ネットに送りません。終わったらメモリから消します。
- 秘密鍵やシードフレーズを、Webサイト・チャット・メール・他人に
  送ることは絶対にしないでください。送るように言う相手は詐欺です。

必要なもの:
- Python 3 と、ライブラリ web3 / eth-account
  （無ければ下の「準備」に出るコマンドを1回だけ実行してください）
- 被害ウォレットの秘密鍵（このパソコンで入力します）
- 安全なウォレット（別のシードフレーズで作った新しいウォレット推奨）の秘密鍵と、
  ガス代ぶんの POL（目安 0.1 POL 以上あると安心です）
- 被害ウォレットに残っている NFT の一覧
  （Alchemy の API キーがあれば自動取得。無ければ手入力もできます）

対象チェーン: Polygon（チェーンID 137）
"""
import getpass
import json
import sys
import urllib.request

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
              "inputs": [{"name": "safeWallet", "type": "address"},
                         {"name": "nftContracts", "type": "address[]"}],
              "outputs": []}]
ABI721 = [{"name": "transferFrom", "type": "function", "stateMutability": "nonpayable",
           "inputs": [{"name": "from", "type": "address"}, {"name": "to", "type": "address"},
                      {"name": "tokenId", "type": "uint256"}], "outputs": []}]
ABI1155 = [
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "account", "type": "address"}, {"name": "id", "type": "uint256"}],
     "outputs": [{"type": "uint256"}]},
    {"name": "safeTransferFrom", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "from", "type": "address"}, {"name": "to", "type": "address"},
                {"name": "id", "type": "uint256"}, {"name": "amount", "type": "uint256"},
                {"name": "data", "type": "bytes"}], "outputs": []},
]


def say(text=""):
    print(text)


def ask(prompt):
    return input(prompt).strip()


def confirm(prompt):
    ans = ask(prompt + "（進むときは「はい」と入力）: ")
    return ans.lower() in ("はい", "hai", "yes", "y", "ok", "おk", "うん")


def ask_key(prompt):
    from eth_account import Account
    say("貼り付け方：先に秘密鍵をコピーしておき、この画面で右クリックすると貼り付きます。")
    say("入力中、文字は画面に表示されませんが、それが正常です（盗み見防止のため）。")
    say("貼れたら Enter を押してください。")
    while True:
        raw = getpass.getpass(prompt).strip().strip('"').strip("'")
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
    sys.exit(0)


def make_w3():
    try:
        from web3 import Web3
        from web3.providers.rpc import HTTPProvider
    except ImportError:
        say("準備が足りません。先に次のコマンドを1回実行してください：")
        say("  python -m pip install web3 eth-account")
        sys.exit(1)

    class RotatingProvider(HTTPProvider):
        """接続先を複数持ち、1つが拒否（403等）されても自動で次に切り替える。"""
        def __init__(self, uris):
            super().__init__(uris[0], request_kwargs={
                "timeout": 20,
                "headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            })
            self._uris = list(uris)

        def make_request(self, method, params):
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
    say("だから攻撃者の自動プログラムと取り合いになりません。")
    say("")
    say("進み方（全部で6ステップ）：")
    say("  1. 被害ウォレットの状態チェック（読むだけ・安全）")
    say("  2. 残っている NFT の一覧を作る")
    say("  3. 安全なウォレットで「救出用の道具（RescueBatch）」を置く")
    say("  4. 被害ウォレットの署名を作る（このパソコンの中だけ）")
    say("  5. 救出の取引を2回送る（委任の付け替え→解除）")
    say("  6. NFT を安全なウォレットへ回収する")
    say("")
    say("途中で止めても、状況が悪くなることはありません。")
    say("分からなくなったら、この画面を閉じて止めて大丈夫です。")
    say("（ステップ5の途中だけは、案内に従って最後まで進めてください）")
    if not confirm("内容が分かったら進みます"):
        stop("問題ありません。準備ができたら、また実行してください。")


def step1_check(w3):
    say("")
    say("── ステップ1：被害ウォレットの状態チェック ──")
    say("アドレスを貼り付けてください（秘密鍵ではありません）。")
    from web3 import Web3
    raw = ask("被害ウォレットのアドレス: ")
    try:
        victim = Web3.to_checksum_address(raw)
    except Exception:
        stop("アドレスの形が正しくありません。0x から始まる42文字のアドレスを貼ってください。")
    st = check_wallet(w3, victim)
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
    else:
        say("⚠️ 心当たりのない委任先です。救出を進めます。")
    return victim, st


def step2_nfts(w3, victim):
    say("")
    say("── ステップ2：残っている NFT の一覧 ──")
    say("救出できるのは「まだ被害ウォレットに残っている NFT」だけです。")
    say("すでに攻撃者のウォレットへ移された分は、取り戻せません。")
    say("")
    say("Alchemy の API キーがあれば自動で一覧を作れます（キーは保存しません）。")
    say("無い場合は、このあと手入力できます。")
    nfts = []
    key = getpass.getpass("Alchemy APIキー（無ければそのまま Enter）: ").strip()
    if key:
        try:
            nfts = fetch_nfts_alchemy(victim, key)
            say(f"取得できました：{len(nfts)} 件")
        except Exception as e:
            say(f"自動取得に失敗しました（{str(e)[:60]}）。手入力に切り替えます。")
            nfts = []
        del key
    if not nfts:
        say("")
        say("NFT を1件ずつ入力してください。形式： コントラクトアドレス,TokenID,種類")
        say("種類は ERC721 か ERC1155。例： 0x1234...abcd,15,ERC721")
        say("1件入れるごとに「他にもありますか？」と聞くので、無いときは「いいえ」と答えてください。")
        while True:
            line = ask("NFTを入力: ").strip()
            if line in ("おわり", "終わり"):
                break
            if not line:
                say("まだ終わりではありません。NFTを入力するか、終わるときは「おわり」と入力してください。")
                continue
            finish = False
            for suf in ("おわり", "終わり"):
                if line.endswith(suf):
                    line = line[: -len(suf)].strip().rstrip(",").strip()
                    finish = True
                    break
            try:
                c, tid, typ = [p.strip() for p in line.split(",")]
                from web3 import Web3
                nfts.append({"contract": Web3.to_checksum_address(c),
                             "tokenId": int(tid), "type": typ.upper()})
            except Exception:
                say("形式が違うようです。もう一度お願いします。")
                continue
            say(f"受け付けました（今 {len(nfts)} 件）")
            if finish:
                break
            if not confirm("他にもありますか"):
                break
    if not nfts:
        stop("NFT が0件です。残っている NFT が無い場合、救出するものがありません。")
    say("")
    say(f"対象の NFT：{len(nfts)} 件")
    for n in nfts:
        say(f"  - {n['contract']} #{n['tokenId']}（{n['type']}）")
    if not confirm("この一覧で合っていますか"):
        stop("止めました。一覧を用意し直してから、また実行してください。")
    return nfts


def send_tx(w3, key, tx):
    import time
    waited = False
    for attempt in range(10):
        tx["nonce"] = w3.eth.get_transaction_count(tx["from"])
        tx["chainId"] = CHAIN_ID
        tx.pop("gasPrice", None)
        gp = w3.eth.gas_price
        tx["maxPriorityFeePerGas"] = int(gp * 0.5)
        tx["maxFeePerGas"] = int(gp * 1.5)
        try:
            bal = w3.eth.get_balance(tx["from"])
            need = int(tx.get("gas", 300000)) * tx["maxFeePerGas"] + int(tx.get("value", 0))
            if bal < need:
                if not waited:
                    say("ガス代が急に高くなっています。少し待って、送れるタイミングを待ちます…")
                    waited = True
                time.sleep(30)
                continue
        except Exception:
            pass
        try:
            signed = w3.eth.account.sign_transaction(tx, key)
            txh = w3.eth.send_raw_transaction(signed.raw_transaction)
            rcpt = w3.eth.wait_for_transaction_receipt(txh, timeout=240)
            return txh, rcpt
        except Exception as e:
            if "insufficient funds" in str(e) and attempt < 9:
                if not waited:
                    say("ガス代が急に高くなっています。少し待って、送れるタイミングを待ちます…")
                    waited = True
                time.sleep(30)
                continue
            raise
    stop("ガス代が高い状態が続いて、送れませんでした。"
         "被害ウォレットやNFTには何も起きていません。"
         "時間をおいて（ガス代が落ち着いてから）スクリプトを再実行してください。")


def step3_deploy(w3, victim):
    say("")
    say("── ステップ3：安全なウォレットの準備と、救出用の道具を置く ──")
    say("安全なウォレットは、被害ウォレットとは別のシードフレーズで作った")
    say("新しいウォレットを強くおすすめします（同じシードだと意味がありません）。")
    say("秘密鍵は画面に表示されません。このパソコンの中だけで使います。")
    from eth_account import Account
    safe_key, safe_acct = ask_key("安全なウォレットの秘密鍵: ")
    safe = safe_acct.address
    if safe.lower() == victim.lower():
        del safe_key
        stop("安全なウォレットと被害ウォレットが同じアドレスです。別のウォレットを用意してください。")
    bal = w3.eth.get_balance(safe) / 1e18
    say(f"安全なウォレット: {safe}")
    say(f"残高: {bal:.6f} POL（ガス代はここから払います）")
    if bal < 0.02:
        say("⚠️ 残高が少ないです。ガス代が足りないと途中で止まります。")
        if not confirm("それでも続けますか"):
            del safe_key
            stop("止めました。安全なウォレットに POL を補充してから再実行してください。")
    say("")
    say("救出用の道具（RescueBatch という小さなプログラム）をチェーン上に置きます。")
    say("ガス代は安全なウォレットが払います。")
    if not confirm("置きますか"):
        del safe_key
        stop("止めました。")
    bytecode = open("rescue_batch_bytecode.txt").read().strip()
    if not bytecode.startswith("0x"):
        bytecode = "0x" + bytecode
    txh, rcpt = send_tx(w3, safe_key, {"from": safe, "data": bytecode, "gas": 300000})
    if rcpt.status != 1:
        del safe_key
        stop(f"道具を置くのに失敗しました（Tx: {txh.hex()}）。時間をおいて再実行してください。")
    batch = rcpt.contractAddress
    say(f"置けました： {batch}")
    return safe, safe_key, batch


def step4_sign(w3, victim, batch):
    say("")
    say("── ステップ4：被害ウォレットの署名を作る ──")
    say("被害ウォレットの秘密鍵を、このパソコンの中だけで使います。")
    say("署名は2つ作ります：①委任を救出用の道具に付け替える ②委任を外す。")
    say("署名したあとは、他の操作をせずにすぐステップ5へ進んでください。")
    say("（間に被害ウォレットで何かすると、番号がずれて署名が無効になります）")
    from eth_account import Account
    victim_key, acct = ask_key("被害ウォレットの秘密鍵: ")
    if acct.address.lower() != victim.lower():
        del victim_key
        stop(f"鍵のアドレス（{acct.address}）が、ステップ1の被害ウォレットと違います。"
             "正しい鍵を入れてください。安全のため止めました。")
    say("鍵を受け付けました（被害ウォレットの鍵で合っています）。")
    nonce = w3.eth.get_transaction_count(victim)
    a1 = Account.sign_authorization(
        {"chainId": CHAIN_ID, "address": batch, "nonce": nonce}, victim_key)
    a2 = Account.sign_authorization(
        {"chainId": CHAIN_ID, "address": ZERO, "nonce": nonce + 1}, victim_key)
    del victim_key

    def dump(a):
        return {"chainId": a.chain_id, "address": a.address, "nonce": a.nonce,
                "yParity": a.y_parity, "r": a.r, "s": a.s}
    say("署名ができました（秘密鍵はメモリから消しました）。")
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
    say("⚠️ Tx1 を送ったら、Tx2 まで一気に進めてください。")
    if not confirm("実行しますか"):
        stop("止めました。署名は無効になるので、やり直すときはステップ4からです"
             "（このスクリプトを再実行してください）。")
    from web3 import Web3
    c = w3.eth.contract(address=Web3.to_checksum_address(batch), abi=ABI_BATCH)
    call = c.functions.approveAll(safe, contracts).build_transaction(
        {"from": safe, "gas": 300000})
    txh1, rcpt1 = send_tx(w3, safe_key, {
        "from": safe, "to": victim, "value": 0, "gas": 200000,
        "data": call["data"], "authorizationList": [auth1]})
    if rcpt1.status != 1:
        stop(f"Tx1 が失敗しました（Tx: {txh1.hex()}）。"
             "署名の番号がずれた可能性があります。スクリプトを再実行して、"
             "ステップ4の署名からやり直してください。")
    say(f"Tx1 完了： {txh1.hex()}")
    txh2, rcpt2 = send_tx(w3, safe_key, {
        "from": safe, "to": victim, "value": 0, "gas": 300000,
        "authorizationList": [auth2]})
    if rcpt2.status != 1:
        stop(f"Tx2 が失敗しました（Tx: {txh2.hex()}）。"
             "委任は救出用の道具に付いたままです。慌てず、スクリプトを再実行して"
             "確認してください。NFTの回収（ステップ6）自体は可能な状態です。")
    say(f"Tx2 完了： {txh2.hex()}")
    st = check_wallet(w3, victim)
    if st["delegated_to"] is None:
        say("確認できました：被害ウォレットの委任は外れました。")
    else:
        say(f"⚠️ 委任がまだ残っています（委任先: {st['delegated_to']}）。")
        say("もう一度チェックし直すか、時間をおいて確認してください。")


def plain_reason(e):
    """エラーの中身を、普通の言葉の理由に変える（生の英語や記号は見せない）。"""
    s = str(e).lower()
    if "not owner" in s or "nonexistent" in s or "invalid token" in s:
        return "被害ウォレットにそのNFTがありませんでした（すでに移されたか、入力間違いの可能性）"
    if "insufficient" in s or "gas" in s:
        return "ガス代まわりで失敗しました（時間をおいて再実行してください）"
    if "403" in s or "429" in s or "timeout" in s or "connection" in s or "rate" in s:
        return "通信が不安定で失敗しました（時間をおいて再実行してください）"
    if s.startswith("tx失敗"):
        return "送るのに失敗しました（時間をおいて再実行してください）"
    return "チェーン側のエラーで失敗しました（時間をおいて再実行すると直る場合があります）"


def step6_recover(w3, safe, safe_key, victim, nfts):
    say("")
    say("── ステップ6：NFT を安全なウォレットへ回収する ──")
    if not confirm(f"{len(nfts)} 件の NFT を回収しますか"):
        stop("回収を止めました。移転許可は出ているので、後からでも回収できます"
             "（transfer_all.py が使えます）。")
    ok, failed = 0, []
    for n in nfts:
        contract, token_id, typ = n["contract"], n["tokenId"], n["type"]
        try:
            if typ == "ERC1155":
                c = w3.eth.contract(address=contract, abi=ABI1155)
                bal = c.functions.balanceOf(victim, token_id).call()
                if bal == 0:
                    failed.append((contract, token_id, "残高0（すでに移された可能性）"))
                    continue
                fn = c.functions.safeTransferFrom(victim, safe, token_id, bal, b"")
            else:
                c = w3.eth.contract(address=contract, abi=ABI721)
                fn = c.functions.transferFrom(victim, safe, token_id)
            tx = fn.build_transaction({"from": safe, "chainId": CHAIN_ID})
            txh, rcpt = send_tx(w3, safe_key, tx)
            if rcpt.status != 1:
                raise RuntimeError(f"Tx失敗 {txh.hex()}")
            ok += 1
            say(f"  OK： {contract} #{token_id}")
        except Exception as e:
            failed.append((contract, token_id, plain_reason(e)))
    say("")
    say(f"回収できた： {ok} 件 ／ できなかった： {len(failed)} 件")
    for contract, token_id, why in failed:
        say(f"  失敗： {contract} #{token_id} — {why}")
    if failed:
        say("できなかった分は、すでに攻撃者へ移された等の理由が考えられます。")
        say("この画面の失敗一覧は、後で確認するために控えておいてください。")


def main():
    w3 = make_w3()
    step0_intro()
    victim, _st = step1_check(w3)
    nfts = step2_nfts(w3, victim)
    safe, safe_key, batch = step3_deploy(w3, victim)
    auth1, auth2 = step4_sign(w3, victim, batch)
    step5_execute(w3, safe, safe_key, victim, batch, nfts, auth1, auth2)
    step6_recover(w3, safe, safe_key, victim, nfts)
    del safe_key
    say("")
    say("=" * 60)
    say("終わりました。最後に大切なこと：")
    say("- 被害ウォレットは、もう使わないでください（鍵が漏れた前提です）。")
    say("- 今後は安全なウォレットを使ってください。")
    say("- 秘密鍵やシードフレーズは、誰にも・どこにも送らないでください。")
    say("=" * 60)


if __name__ == "__main__":
    main()
