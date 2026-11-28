"""On this cluster the built-in `view` ClusterRole READS EVERY SECRET.

The incident this encodes (SA token audit 2026-11-10, infra-p6q07.3): Harvester's chart
ships `harvesterhci.io:view` labelled `rbac.authorization.k8s.io/aggregate-to-view: "true"`,
and one of its rules is `secrets: get/list/watch`. The aggregation controller folds it into
`view`, so the textbook guarantee "view excludes Secrets by design" is false here. The
k8s-mcp ServiceAccount was bound to `view` cluster-wide on exactly that belief (its RBAC
comment said so), which gave it the two long-lived cluster-admin token Secrets and the
Akeyless auth Secret: read-only on paper, cluster-admin in practice.

So no manifest here may bind `view` (or the `edit`/`admin` roles that aggregate it). A
read role is our own ClusterRole whose aggregationRule excludes Harvester's role by label.
"""

import unittest

import fleetlib

BINDINGS = {"ClusterRoleBinding", "RoleBinding"}
AGGREGATING_VIEW = {"view", "edit", "admin"}
VIEW_LABEL = "rbac.authorization.k8s.io/aggregate-to-view"
HARVESTER_EXCLUSION = {"key": "app.kubernetes.io/name", "operator": "NotIn", "values": ["harvester"]}


def _docs():
    for rel in fleetlib.tracked_yaml():
        for doc in fleetlib.load_docs(rel):
            yield rel, doc


def _name(doc):
    return (doc.get("metadata") or {}).get("name", "<unnamed>")


def binding_violations(docs):
    out = []
    for rel, doc in docs:
        if doc.get("kind") not in BINDINGS:
            continue
        ref = doc.get("roleRef") or {}
        if ref.get("kind") == "ClusterRole" and ref.get("name") in AGGREGATING_VIEW:
            out.append(f"{rel}: {doc['kind']}/{_name(doc)} binds ClusterRole/{ref['name']}, "
                       f"which reads Secrets here (harvesterhci.io:view aggregates them)")
    return out


def _selects_view(selector):
    if (selector.get("matchLabels") or {}).get(VIEW_LABEL) == "true":
        return True
    return any(e.get("key") == VIEW_LABEL and e.get("operator") == "In" and "true" in e.get("values", [])
               for e in selector.get("matchExpressions") or [])


def aggregation_violations(docs):
    out = []
    for rel, doc in docs:
        if doc.get("kind") != "ClusterRole":
            continue
        for sel in (doc.get("aggregationRule") or {}).get("clusterRoleSelectors") or []:
            if _selects_view(sel) and HARVESTER_EXCLUSION not in (sel.get("matchExpressions") or []):
                out.append(f"{rel}: ClusterRole/{_name(doc)} aggregates the view roles without "
                           f"excluding {HARVESTER_EXCLUSION} -- it inherits secrets get/list/watch")
    return out


class ViewReadsSecretsTest(unittest.TestCase):
    def test_no_binding_to_a_view_aggregating_role(self):
        self.assertEqual([], binding_violations(_docs()))

    def test_view_aggregations_exclude_harvester(self):
        self.assertEqual([], aggregation_violations(_docs()))

    # The checks must be able to fail: pin them on synthetic documents.
    def test_detects_a_view_binding(self):
        doc = {"kind": "ClusterRoleBinding", "metadata": {"name": "x"},
               "roleRef": {"kind": "ClusterRole", "name": "view"}}
        self.assertEqual(1, len(binding_violations([("x.yml", doc)])))

    def test_detects_an_unfenced_view_aggregation(self):
        bare = {"kind": "ClusterRole", "metadata": {"name": "x"}, "aggregationRule": {
            "clusterRoleSelectors": [{"matchLabels": {VIEW_LABEL: "true"}}]}}
        fenced = {"kind": "ClusterRole", "metadata": {"name": "y"}, "aggregationRule": {
            "clusterRoleSelectors": [{"matchExpressions": [
                {"key": VIEW_LABEL, "operator": "In", "values": ["true"]}, HARVESTER_EXCLUSION]}]}}
        self.assertEqual(1, len(aggregation_violations([("x.yml", bare)])))
        self.assertEqual([], aggregation_violations([("y.yml", fenced)]))


if __name__ == "__main__":
    unittest.main()
