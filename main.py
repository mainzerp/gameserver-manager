import os

import uvicorn

from app.config import settings

if __name__ == "__main__":
    kwargs = {
        "app": "app.main:app",
        "host": settings.host,
        "port": settings.port,
        "reload": settings.debug,
        # Trust X-Forwarded-For from reverse proxies
        # Restrict forwarded_allow_ips to proxy IP in production for security
        "proxy_headers": True,
        "forwarded_allow_ips": os.environ.get("GSM_TRUSTED_PROXY", "127.0.0.1"),
    }

    if settings.ssl_enabled:
        if settings.ssl_certfile and settings.ssl_keyfile:
            if os.path.isfile(settings.ssl_certfile) and os.path.isfile(
                settings.ssl_keyfile
            ):
                kwargs["ssl_certfile"] = settings.ssl_certfile
                kwargs["ssl_keyfile"] = settings.ssl_keyfile
            else:
                print(
                    "WARNING: SSL enabled but cert/key files not found, starting without SSL"
                )
        else:
            print(
                "WARNING: SSL enabled but cert/key paths not configured, starting without SSL"
            )

    uvicorn.run(**kwargs)
