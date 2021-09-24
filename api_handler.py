import os
import json
import hashlib
from uuid import uuid4
import aiofiles
from aiohttp import ClientSession


class CoWin:
    def __init__(self) -> None:
        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            'User-Agent': 'Mozilla/5.0 (Windows; Intel Mac OS X 10_15_4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/83.0.4103.61 Safari/537.36',
        }

    async def call_api(self, endpoint: str, body: dict = None, headers: dict = {}, method: str = "POST", inbytes: bool = False):
        base_url = "https://cdn-api.co-vin.in/api"
        for key, value in headers.items():
            self.headers[key] = value
        async with ClientSession(headers=self.headers) as session:
            async with session.request(method, base_url + endpoint, json=body) as resp:
                if inbytes:
                    return resp.status, await resp.read()
                res = await resp.text()
                try:
                    res = json.loads(res)
                finally:
                    return resp.status, res

    async def generate_otp(self, phone_no: str, secret: dict):
        return await self.call_api("/v2/auth/generateMobileOTP", {
            "mobile": phone_no,
            "secret": secret
        })

    async def confirm_otp(self, otp: str, txnid: str):
        return await self.call_api("/v2/auth/validateMobileOtp", {
            "otp": hashlib.sha256(otp.encode()).hexdigest(),
            "txnId": txnid
        })

    async def list_beneficiaries(self, auth_token: str):
        return await self.call_api("/v2/appointment/beneficiaries", headers={
            "Authorization": f"Bearer {auth_token}"
        }, method="GET")

    async def download_certificate(self, auth_token: str, ben_id: str):
        code, data = await self.call_api(f"/v2/registration/certificate/download?beneficiary_reference_id={ben_id}", headers={
            "Authorization": f"Bearer {auth_token}"
        }, method="GET", inbytes=True)
        if code != 200:
            return
        base_path = "./pdfs"
        if not os.path.exists(base_path):
            os.mkdir(base_path)
        file_path = os.path.join(base_path, f"./{str(uuid4())}.pdf")
        async with aiofiles.open(file_path, "wb") as f:
            await f.write(data)
        return file_path
