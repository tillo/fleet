#!/bin/sh

set -e

notify() {
  printf '%s\n' "$1"
  printf '%s ' "$1" >> /dev/termination-log
}

major_of()
{
  printf '%s' "${1%%.*}"
}

minor_of()
{
  rest=${1#*.}
  printf '%s' "${rest%%.*}"
}

greater_version()
{
  [ "$(major_of "$1")" -gt "$(major_of "$2")" ] && return 0
  [ "$(major_of "$1")" -eq "$(major_of "$2")" ] && [ "$(minor_of "$1")" -ge "$(minor_of "$2")" ]
}

is_digits()
{
  case "$1" in
    ''|*[!0-9]*) return 1 ;;
  esac
}

is_semver()
{
  major=${1%%.*}
  rest=${1#*.}
  minor=${rest%%.*}
  patch=${rest#*.}
  patch=${patch%%[!0-9]*}
  [ "$major" != "$1" ] && [ "$minor" != "$rest" ] || return 1
  is_digits "$major" && is_digits "$minor" && is_digits "$patch" || return 1
  [ "${#major}" -le 3 ] && [ "${#minor}" -le 3 ]
}

MIN_VERSION=19.5
CHART_MIN_VERSION=10.5

# Remove 'v' prefix from GitLab version if present (set in Chart.yaml appVersions)
GITLAB_VERSION=${GITLAB_VERSION#v}

# Only run check for semver releases
if ! is_semver "$GITLAB_VERSION"; then
  exit 0
fi

if [ ! -f /chart-info/gitlabVersion ]; then
  notify "It seems you are attempting an unsupported upgrade path."
  notify "Please follow the upgrade documentation at https://docs.gitlab.com/update/upgrade_paths/"
  exit 1
fi

read -r OLD_VERSION_STRING < /chart-info/gitlabVersion || [ -f /chart-info/gitlabVersion ]
OLD_VERSION_STRING=${OLD_VERSION_STRING#v}
read -r OLD_CHART_VERSION_STRING < /chart-info/gitlabChartVersion || [ -f /chart-info/gitlabChartVersion ]

# Skip check if old version wasn't semver
if ! is_semver "$OLD_VERSION_STRING"; then
  exit 0
fi

# Checking Version
if ! greater_version "$OLD_VERSION_STRING" "$MIN_VERSION" || ! greater_version "$OLD_CHART_VERSION_STRING" "$CHART_MIN_VERSION"; then
  notify "It seems you are upgrading the GitLab Helm Chart from ${OLD_CHART_VERSION_STRING} (GitLab ${OLD_VERSION_STRING}) to ${CHART_VERSION} (GitLab ${GITLAB_VERSION})."
  notify "It is required to upgrade to the latest ${CHART_MIN_VERSION}.x version first before proceeding."
  # Link to upgrade docs if it is a major version jump
  if [ "$(major_of "$OLD_VERSION_STRING")" -lt "$(major_of "$GITLAB_VERSION")" ] || [ "$(major_of "$OLD_CHART_VERSION_STRING")" -lt "$(major_of "$CHART_VERSION")" ]; then
    notify "Please follow the upgrade documentation at https://docs.gitlab.com/charts/releases/8_0/"
    notify "and upgrade to GitLab Helm Chart version ${CHART_MIN_VERSION}.x before upgrading to ${CHART_VERSION}."
  fi
  exit 1
fi