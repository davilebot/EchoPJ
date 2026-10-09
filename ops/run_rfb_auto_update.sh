#!/bin/sh
set -eu

project_directory=/opt/echopjs-saas-v2
compose_file="$project_directory/compose.saas-v2.yml"
app_container=echopjs-saas_v2-app-1
app_image="$(docker inspect --format '{{.Config.Image}}' "$app_container")"

if [ -z "${RFB_UPDATER_IMAGE:-}" ]; then
  RFB_UPDATER_IMAGE="$app_image"
  export RFB_UPDATER_IMAGE
fi
if [ -z "${SAAS_IMAGE_TAG:-}" ]; then
  SAAS_IMAGE_TAG="${app_image#echopjs-saas:}"
  export SAAS_IMAGE_TAG
fi

exec docker compose \
  --project-name echopjs-saas_v2 \
  --file "$compose_file" \
  --profile operations \
  run --rm rfb-updater
