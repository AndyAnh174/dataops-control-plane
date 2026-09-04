# Demo VPS deployment

This deployment keeps the demo application on `dataops.andyanh.id.vn` and exposes the
Control Plane separately at `https://dataops-console.andyanh.id.vn`.

The Compose file is deployed to `192.168.1.53:/opt/dataops-demo/control-plane/compose.yaml`.
Runtime secrets remain only in the existing `control-plane.env` and `.env` files on that host.
Nginx runs on `192.168.1.80`; only the DataOps Console site is managed by these files.

## Deploy

Back up PostgreSQL before deploying a version that may create or migrate tables. Then validate
the rendered Compose configuration and roll out only the API so the existing data services are
not recreated:

```bash
cd /opt/dataops-demo/control-plane
docker compose --env-file control-plane.env --env-file .env config --quiet
docker compose --env-file control-plane.env --env-file .env pull api
docker compose --env-file control-plane.env --env-file .env up -d --no-deps --pull never api
docker compose --env-file control-plane.env --env-file .env ps api postgres elasticsearch
```

For a new proxy, install `nginx-http.conf` first, request the certificate, then replace it with
`nginx.conf`. Always run `nginx -t` before reloading Nginx. The final configuration redirects HTTP
to HTTPS and enables HSTS.

```bash
certbot --nginx -d dataops-console.andyanh.id.vn
nginx -t
systemctl reload nginx
```

## Verify

```bash
curl --fail http://127.0.0.1:18080/health
curl --fail http://192.168.1.53:18080/health
curl --fail https://dataops-console.andyanh.id.vn/health
```

## Roll back

Change the API image to the previous known-good immutable tag:

```text
ghcr.io/andyanh174/dataops-control-plane:sha-058bb771fb67b1201f147a1b7a9f85f174578211
```

Then run the same `config`, `pull api`, API-only rollout and health checks. Do not remove the
PostgreSQL or Elasticsearch volumes during rollback.
