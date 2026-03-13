"""
Vercel API client — auto-provisions tenant subdomains on tenant creation.

Setup:
  1. Go to https://vercel.com/account/tokens
  2. Create a new token, name it "Insurance RAG Backend"
  3. Go to your client-frontend project → Settings → General
  4. Copy the "Project ID" (looks like: prj_xxxxxxxxxxxx)
  5. Set these env vars:
     VERCEL_TOKEN=<token>
     VERCEL_PROJECT_ID=<project_id>
     VERCEL_TEAM_ID=<team_id or empty if personal account>

Usage:
    from app.services.vercel_domains import VercelDomainService

    vercel = VercelDomainService()
    result = await vercel.add_domain("levanti")
    # result = {"domain": "levanti.agencylensai.com", "verified": True}
"""

import logging
import httpx
from typing import Optional

from app.config import get_settings

logger = logging.getLogger("api.vercel_domains")
settings = get_settings()

BASE_DOMAIN = "agencylensai.com"


class VercelDomainService:
    def __init__(self):
        self.token = getattr(settings, "vercel_token", None) or ""
        self.project_id = getattr(settings, "vercel_project_id", None) or ""
        self.team_id = getattr(settings, "vercel_team_id", None) or ""
        self.enabled = bool(self.token and self.project_id)

        if not self.enabled:
            logger.warning(
                "Vercel domain provisioning not configured — "
                "subdomains must be added manually in the Vercel dashboard."
            )

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    def _params(self) -> dict:
        """Add teamId to all requests if configured."""
        if self.team_id:
            return {"teamId": self.team_id}
        return {}

    async def add_domain(self, slug: str) -> dict:
        """
        Register {slug}.agencylensai.com on the Vercel project.

        Returns:
            {
                "domain": "levanti.agencylensai.com",
                "verified": True/False,
                "auto_provisioned": True/False,
                "error": None or error message
            }
        """
        domain = f"{slug}.{BASE_DOMAIN}"

        if not self.enabled:
            logger.info(f"Vercel not configured, skipping domain: {domain}")
            return {
                "domain": domain,
                "verified": False,
                "auto_provisioned": False,
                "error": "Vercel API not configured",
            }

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"https://api.vercel.com/v10/projects/{self.project_id}/domains",
                    headers=self._headers(),
                    params=self._params(),
                    json={"name": domain},
                    timeout=30,
                )

                if resp.status_code in (200, 201):
                    data = resp.json()
                    logger.info(f"Vercel domain added: {domain}")
                    return {
                        "domain": domain,
                        "verified": data.get("verified", False),
                        "auto_provisioned": True,
                        "error": None,
                    }
                elif resp.status_code == 409:
                    # Domain already exists
                    logger.info(f"Vercel domain already exists: {domain}")
                    return {
                        "domain": domain,
                        "verified": True,
                        "auto_provisioned": False,
                        "error": None,
                    }
                else:
                    error_msg = resp.json().get("error", {}).get("message", resp.text)
                    logger.error(f"Vercel domain add failed for {domain}: {resp.status_code} {error_msg}")
                    return {
                        "domain": domain,
                        "verified": False,
                        "auto_provisioned": False,
                        "error": error_msg,
                    }

        except Exception as e:
            logger.error(f"Vercel API error for {domain}: {e}")
            return {
                "domain": domain,
                "verified": False,
                "auto_provisioned": False,
                "error": str(e),
            }

    async def remove_domain(self, slug: str) -> bool:
        """Remove a tenant subdomain from Vercel. Returns True if successful."""
        domain = f"{slug}.{BASE_DOMAIN}"

        if not self.enabled:
            return False

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.delete(
                    f"https://api.vercel.com/v9/projects/{self.project_id}/domains/{domain}",
                    headers=self._headers(),
                    params=self._params(),
                    timeout=15,
                )

                if resp.status_code in (200, 204):
                    logger.info(f"Vercel domain removed: {domain}")
                    return True
                elif resp.status_code == 404:
                    logger.info(f"Vercel domain not found (already removed): {domain}")
                    return True
                else:
                    logger.error(f"Vercel domain remove failed: {resp.status_code} {resp.text}")
                    return False

        except Exception as e:
            logger.error(f"Vercel API error removing {domain}: {e}")
            return False

    async def check_domain(self, slug: str) -> dict:
        """Check if a domain is configured and verified on Vercel."""
        domain = f"{slug}.{BASE_DOMAIN}"

        if not self.enabled:
            return {"domain": domain, "configured": False, "verified": False}

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"https://api.vercel.com/v9/projects/{self.project_id}/domains/{domain}",
                    headers=self._headers(),
                    params=self._params(),
                    timeout=10,
                )

                if resp.status_code == 200:
                    data = resp.json()
                    return {
                        "domain": domain,
                        "configured": True,
                        "verified": data.get("verified", False),
                    }
                else:
                    return {"domain": domain, "configured": False, "verified": False}

        except Exception:
            return {"domain": domain, "configured": False, "verified": False}