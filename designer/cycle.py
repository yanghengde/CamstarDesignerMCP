"""One complete project cycle for browser, chat and MCP publication entry points."""
from designer import publication, review, working


def final_manifest(manifest_file):
    path, manifest = publication.verified_manifest(manifest_file)
    working.check_current(manifest)
    project_id = manifest.get('working_project_id')
    if not project_id:
        return str(path)
    with working.project_lock(project_id):
        state = working.load(project_id)
        paths = state.get('cycle_manifests') or [state['latest_manifest']]
        if manifest.get('owned_source_manifests') == paths:
            return str(path)
        if str(path) != state['latest_manifest']:
            raise ValueError('工作 MDB 已更新，请使用最新设计清单')
        cached = state.get('cycle_manifest')
        if cached and state.get('cycle_manifest_sources') == paths:
            try:
                _, saved = publication.verified_manifest(cached)
                working.check_current(saved)
                return cached
            except (ValueError, OSError, KeyError):
                pass
        from designer.progress_actions import combined_manifest
        combined = combined_manifest(review.summary(str(path)), paths)
        state = working.load(project_id)
        working.write_json(working.project_dir(project_id) / 'state.json',
                           {**state, 'cycle_manifest': combined, 'cycle_manifest_sources': paths})
        return combined


def prepare_plan(manifest_file):
    from designer.progress_actions import prepare_plan as prepare
    path = final_manifest(manifest_file)
    return prepare(review.summary(path), [path], final_file=path)
