//! A single installer cache, bound to the checked feed's URL/version/signature.
//! ETags permit transport resumption; only Minisign can establish installability.
use base64::Engine;
use minisign_verify::{PublicKey, Signature};
use serde::{Deserialize, Serialize};
use std::{
    fs,
    io::Write,
    path::{Path, PathBuf},
    sync::atomic::{AtomicBool, Ordering},
};
use tauri::ipc::Channel;
use tauri_plugin_updater::Update;

const MAX_INSTALLER_BYTES: u64 = 250 * 1024 * 1024;
const CANCELLED: &str = "Update download paused. Saved bytes are kept for the next attempt.";

#[derive(Clone)]
struct Artifact {
    version: String,
    download_url: reqwest::Url,
    signature: String,
    key: String,
}
impl Artifact {
    fn from_update(update: &Update) -> Result<Self, String> {
        let config: serde_json::Value = serde_json::from_str(include_str!("../tauri.conf.json"))
            .map_err(|_| "Invalid embedded update configuration")?;
        let key = config["plugins"]["updater"]["pubkey"]
            .as_str()
            .ok_or("Missing embedded update key")?;
        Ok(Self {
            version: update.version.clone(),
            download_url: update.download_url.clone(),
            signature: update.signature.clone(),
            key: key.into(),
        })
    }
}

#[derive(Clone, Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CacheStatus {
    pub download_state: String,
    pub downloaded: u64,
    pub total: Option<u64>,
    pub resumable: bool,
}

#[derive(Serialize, Deserialize, Clone)]
struct Metadata {
    version: String,
    url: String,
    signature: String,
    etag: Option<String>,
    total: Option<u64>,
    complete: bool,
}

impl Metadata {
    fn matches(&self, update: &Artifact) -> bool {
        self.version == update.version
            && self.url == update.download_url.as_str()
            && self.signature == update.signature
    }
    fn new(update: &Artifact) -> Self {
        Self {
            version: update.version.clone(),
            url: update.download_url.to_string(),
            signature: update.signature.clone(),
            etag: None,
            total: None,
            complete: false,
        }
    }
}

fn metadata(root: &Path) -> Option<Metadata> {
    if file_length(&root.join("metadata.json")) > 64 * 1024 {
        return None;
    }
    let bytes = fs::read(root.join("metadata.json")).ok()?;
    if bytes.len() > 64 * 1024 {
        return None;
    }
    serde_json::from_slice(&bytes).ok()
}

fn save_metadata(root: &Path, meta: &Metadata) -> Result<(), String> {
    let bytes = serde_json::to_vec(meta).map_err(|_| "Cannot save update metadata")?;
    let temporary = root.join("metadata.tmp");
    let mut file = fs::File::create(&temporary).map_err(|_| "Cannot save update metadata")?;
    file.write_all(&bytes)
        .and_then(|_| file.sync_all())
        .map_err(|_| "Cannot save update metadata")?;
    // Windows rename cannot replace an existing destination. A crash in this
    // small gap loses resumption metadata, never signature verification.
    if root.join("metadata.json").exists() {
        fs::remove_file(root.join("metadata.json"))
            .map_err(|_| "Cannot replace update metadata")?;
    }
    fs::rename(temporary, root.join("metadata.json"))
        .map_err(|_| "Cannot save update metadata".into())
}

fn file_length(path: &Path) -> u64 {
    fs::metadata(path).map(|m| m.len()).unwrap_or(0)
}

fn strong_etag(value: &str) -> bool {
    value.len() >= 2
        && value.starts_with('"')
        && value.ends_with('"')
        && !value.contains(['\r', '\n'])
}

fn usable_partial(meta: &Metadata, length: u64) -> bool {
    !meta.complete
        && length > 0
        && length <= MAX_INSTALLER_BYTES
        && meta
            .total
            .is_some_and(|total| length < total && total <= MAX_INSTALLER_BYTES)
        && meta.etag.as_deref().is_some_and(strong_etag)
}

pub fn verify_signed_version(comment: &str, announced: &str) -> Result<(), String> {
    let signed = comment
        .split('\t')
        .find_map(|field| field.strip_prefix("version:"))
        .ok_or("The update has no signed version. It cannot be installed.")?;
    let expected = semver::Version::parse(announced.trim_start_matches('v'))
        .map_err(|_| "Invalid update version")?;
    let actual = semver::Version::parse(signed.trim_start_matches('v'))
        .map_err(|_| "Invalid signed update version")?;
    if expected != actual {
        return Err("The signed installer version does not match this update.".into());
    }
    Ok(())
}

pub fn verify_with_key(
    bytes: &[u8],
    encoded_signature: &str,
    encoded_key: &str,
    version: &str,
) -> Result<(), String> {
    let decode = |value: &str| -> Result<String, String> {
        let bytes = base64::engine::general_purpose::STANDARD
            .decode(value)
            .map_err(|_| "Invalid update signature encoding")?;
        String::from_utf8(bytes).map_err(|_| "Invalid update signature text".into())
    };
    let key =
        PublicKey::decode(&decode(encoded_key)?).map_err(|_| "Invalid embedded update key")?;
    let signature =
        Signature::decode(&decode(encoded_signature)?).map_err(|_| "Invalid update signature")?;
    // This verifies both the artifact and the global signature over its trusted
    // comment, exactly as pinned tauri-plugin-updater 2.13.1 does. Only then may
    // the signed version in that comment be read.
    key.verify(bytes, &signature, true)
        .map_err(|_| "The update signature is invalid. Download it again.")?;
    verify_signed_version(signature.trusted_comment(), version)
}

fn verify_artifact(bytes: &[u8], update: &Artifact) -> Result<(), String> {
    verify_with_key(bytes, &update.signature, &update.key, &update.version)
}

pub fn checked_bytes(root: &Path, update: &Update) -> Result<Vec<u8>, String> {
    checked_artifact_bytes(root, &Artifact::from_update(update)?)
}

fn checked_artifact_bytes(root: &Path, update: &Artifact) -> Result<Vec<u8>, String> {
    let meta = metadata(root)
        .filter(|m| m.matches(update))
        .ok_or("Download and verify the update first")?;
    let path = root.join("installer.exe");
    let len = file_length(&path);
    if len == 0 || len > MAX_INSTALLER_BYTES || meta.total.is_some_and(|total| total != len) {
        return Err("The cached update is incomplete. Download it again.".into());
    }
    let bytes = fs::read(path).map_err(|_| "Cannot read the downloaded update")?;
    verify_artifact(&bytes, update)?;
    Ok(bytes)
}

pub fn status(root: &Path, update: &Update) -> CacheStatus {
    match Artifact::from_update(update) {
        Ok(artifact) => artifact_status(root, &artifact),
        Err(_) => CacheStatus {
            download_state: "available".into(),
            ..Default::default()
        },
    }
}

fn artifact_status(root: &Path, update: &Artifact) -> CacheStatus {
    let Some(meta) = metadata(root).filter(|m| m.matches(update)) else {
        return CacheStatus {
            download_state: "available".into(),
            ..Default::default()
        };
    };
    if checked_artifact_bytes(root, update).is_ok() {
        return CacheStatus {
            download_state: "downloaded".into(),
            downloaded: file_length(&root.join("installer.exe")),
            total: meta.total,
            resumable: false,
        };
    }
    let length = file_length(&root.join("installer.partial"));
    let fully_saved = !meta.complete
        && length > 0
        && length <= MAX_INSTALLER_BYTES
        && meta.total == Some(length)
        && fs::read(root.join("installer.partial"))
            .is_ok_and(|bytes| verify_artifact(&bytes, update).is_ok());
    CacheStatus {
        download_state: if length > 0 { "paused" } else { "available" }.into(),
        downloaded: length,
        total: meta.total,
        resumable: usable_partial(&meta, length) || fully_saved,
    }
}

fn validated_asset_url(update: &Update) -> Result<(), String> {
    let url = &update.download_url;
    if url.scheme() != "https"
        || url.host_str() != Some("github.com")
        || !url.username().is_empty()
        || url.password().is_some()
        || !url
            .path()
            .starts_with("/MunawarAliAraiz/AI-Voice-Clone/releases/download/")
    {
        return Err("This update does not use the approved release download location.".into());
    }
    Ok(())
}

fn parse_content_range(value: &str) -> Option<(u64, u64, u64)> {
    let (range, total) = value.strip_prefix("bytes ")?.split_once('/')?;
    let (start, end) = range.split_once('-')?;
    let (start, end, total) = (start.parse().ok()?, end.parse().ok()?, total.parse().ok()?);
    if start > end || end >= total || total > MAX_INSTALLER_BYTES {
        return None;
    }
    Some((start, end, total))
}

fn valid_range(
    meta: &Metadata,
    offset: u64,
    etag: Option<&str>,
    range: Option<&str>,
    length: Option<u64>,
) -> bool {
    let Some((start, end, total)) = range.and_then(parse_content_range) else {
        return false;
    };
    start == offset
        && Some(total) == meta.total
        && etag == meta.etag.as_deref()
        && length == Some(end - start + 1)
        && end + 1 == total
}

pub async fn download(
    root: PathBuf,
    update: Update,
    cancel: &AtomicBool,
    progress: Channel<super::DownloadProgress>,
) -> Result<CacheStatus, String> {
    validated_asset_url(&update)?;
    download_artifact(root, Artifact::from_update(&update)?, cancel, progress).await
}

// Only the production wrapper above can enter here from an IPC command. Unit
// tests exercise transport with a loopback server and a separate fixture key.
async fn download_artifact(
    root: PathBuf,
    update: Artifact,
    cancel: &AtomicBool,
    progress: Channel<super::DownloadProgress>,
) -> Result<CacheStatus, String> {
    fs::create_dir_all(&root).map_err(|_| "Cannot create the local update cache")?;
    if checked_artifact_bytes(&root, &update).is_ok() {
        return Ok(artifact_status(&root, &update));
    }
    let partial = root.join("installer.partial");
    let existing = metadata(&root).filter(|m| m.matches(&update));
    let mut meta = existing.clone().unwrap_or_else(|| Metadata::new(&update));
    let mut offset = file_length(&partial);
    // Cancellation/crash can occur after the last byte but before completion
    // metadata is written. Verify and promote that complete prefix locally.
    if !meta.complete && offset > 0 && offset <= MAX_INSTALLER_BYTES && meta.total == Some(offset) {
        if let Ok(bytes) = fs::read(&partial) {
            if verify_artifact(&bytes, &update).is_ok() {
                finish_partial(&root, &partial, &mut meta, offset)?;
                return Ok(artifact_status(&root, &update));
            }
        }
    }
    if !usable_partial(&meta, offset) {
        offset = 0;
        meta = Metadata::new(&update);
    }
    let client = reqwest::Client::builder()
        .connect_timeout(std::time::Duration::from_secs(15))
        .read_timeout(std::time::Duration::from_secs(30))
        .timeout(std::time::Duration::from_secs(900))
        .redirect(reqwest::redirect::Policy::custom(|attempt| {
            if attempt.previous().len() >= 10 || attempt.url().scheme() != "https" {
                attempt.stop()
            } else {
                attempt.follow()
            }
        }))
        .build()
        .map_err(|_| "Cannot prepare the update connection")?;
    let mut request = client
        .get(update.download_url.clone())
        .header("Accept-Encoding", "identity");
    if offset > 0 {
        request = request
            .header("Range", format!("bytes={offset}-"))
            .header("If-Range", meta.etag.as_deref().unwrap_or_default());
    }
    let mut response = request.send().await.map_err(|_| {
        if cancel.load(Ordering::Acquire) {
            CANCELLED
        } else {
            "Update connection failed. Saved download bytes have been kept; retry when connected."
        }
    })?;
    if cancel.load(Ordering::Acquire) {
        return Ok(artifact_status(&root, &update));
    }
    if offset > 0 {
        let acceptable = response.status() == reqwest::StatusCode::PARTIAL_CONTENT
            && valid_range(
                &meta,
                offset,
                response.headers().get("ETag").and_then(|v| v.to_str().ok()),
                response
                    .headers()
                    .get("Content-Range")
                    .and_then(|v| v.to_str().ok()),
                response.content_length(),
            );
        if !acceptable {
            // A changed entity, ignored Range, or invalid range is never appended.
            // Obtain a fresh full response and overwrite the partial file below.
            offset = 0;
            meta = Metadata::new(&update);
            response = client
                .get(update.download_url.clone())
                .header("Accept-Encoding", "identity")
                .send()
                .await
                .map_err(|_| "Cannot restart the update download; retry when connected")?;
        }
    }
    if cancel.load(Ordering::Acquire) {
        return Ok(artifact_status(&root, &update));
    }
    if (offset == 0 && response.status() != reqwest::StatusCode::OK)
        || (offset > 0 && response.status() != reqwest::StatusCode::PARTIAL_CONTENT)
        || response
            .headers()
            .get("Content-Encoding")
            .is_some_and(|v| v != "identity")
    {
        return Err(
            "The update server did not return a valid installer download. Saved bytes were kept."
                .into(),
        );
    }
    if offset == 0 {
        meta.total = response.content_length();
        meta.etag = response
            .headers()
            .get("ETag")
            .and_then(|v| v.to_str().ok())
            .filter(|value| strong_etag(value))
            .map(str::to_owned);
    }
    if meta
        .total
        .is_some_and(|total| total == 0 || total > MAX_INSTALLER_BYTES)
    {
        return Err("The update size is outside the supported installer limit.".into());
    }
    meta.complete = false;
    // Opening/truncating before publishing metadata prevents metadata for the
    // new entity from ever describing bytes left from the previous entity.
    let mut file = fs::OpenOptions::new()
        .create(true)
        .write(true)
        .append(offset > 0)
        .truncate(offset == 0)
        .open(&partial)
        .map_err(|_| "Cannot save the update download")?;
    save_metadata(&root, &meta)?;
    let mut downloaded = offset;
    let mut last_progress: Option<std::time::Instant> = None;
    let _ = progress.send(super::DownloadProgress {
        downloaded,
        total: meta.total,
    });
    loop {
        if cancel.load(Ordering::Acquire) {
            break;
        }
        let chunk = match response.chunk().await {
            Ok(value) => value,
            Err(_) if cancel.load(Ordering::Acquire) => break,
            Err(_) => {
                let _ = file.sync_all();
                return Err(
                    "Update download interrupted. Saved bytes were kept; resume when connected."
                        .into(),
                );
            }
        };
        let Some(chunk) = chunk else {
            break;
        };
        if cancel.load(Ordering::Acquire) {
            break;
        }
        downloaded += chunk.len() as u64;
        if downloaded > MAX_INSTALLER_BYTES || meta.total.is_some_and(|total| downloaded > total) {
            return Err(
                "The update server returned more bytes than expected. It cannot be installed."
                    .into(),
            );
        }
        file.write_all(&chunk)
            .map_err(|_| "Cannot save the update download; check free disk space")?;
        // Keep the UI responsive on fast links instead of emitting one IPC
        // message per tiny network chunk. Counters always use actual bytes.
        if last_progress.is_none_or(|last| last.elapsed() >= std::time::Duration::from_millis(100))
            || meta.total == Some(downloaded)
        {
            let _ = progress.send(super::DownloadProgress {
                downloaded,
                total: meta.total,
            });
            last_progress = Some(std::time::Instant::now());
        }
    }
    file.sync_all()
        .map_err(|_| "Cannot finish saving the update download")?;
    drop(file);
    if cancel.load(Ordering::Acquire) {
        return Ok(artifact_status(&root, &update));
    }
    if meta.total.is_some_and(|total| total != downloaded) {
        return Err("The update is incomplete. Saved bytes were kept; resume the download.".into());
    }
    let bytes = fs::read(&partial).map_err(|_| "Cannot verify the downloaded update")?;
    if let Err(error) = verify_artifact(&bytes, &update) {
        // Corrupt/invalid bytes cannot be resumed or offered as installable.
        let _ = fs::remove_file(&partial);
        let _ = fs::remove_file(root.join("metadata.json"));
        return Err(error);
    }
    if cancel.load(Ordering::Acquire) {
        return Ok(artifact_status(&root, &update));
    }
    finish_partial(&root, &partial, &mut meta, downloaded)?;
    Ok(CacheStatus {
        download_state: "downloaded".into(),
        downloaded,
        total: Some(downloaded),
        resumable: false,
    })
}

fn finish_partial(
    root: &Path,
    partial: &Path,
    meta: &mut Metadata,
    length: u64,
) -> Result<(), String> {
    if root.join("installer.exe").exists() {
        fs::remove_file(root.join("installer.exe"))
            .map_err(|_| "Cannot replace the cached installer")?;
    }
    fs::rename(partial, root.join("installer.exe"))
        .map_err(|_| "Cannot keep the verified installer")?;
    meta.complete = true;
    meta.total = Some(length);
    save_metadata(root, meta)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{io::Read, net::TcpListener, sync::Arc};
    fn fixture() -> (Artifact, Vec<u8>) {
        let value: serde_json::Value =
            serde_json::from_str(include_str!("updater-test-fixture.json")).unwrap();
        (
            Artifact {
                version: value["version"].as_str().unwrap().into(),
                download_url: "http://127.0.0.1:1/fixture".parse().unwrap(),
                signature: value["signature"].as_str().unwrap().into(),
                key: value["key"].as_str().unwrap().into(),
            },
            value["payload"].as_str().unwrap().as_bytes().to_vec(),
        )
    }
    struct TestRoot(PathBuf);
    impl TestRoot {
        fn new() -> Self {
            let path = std::env::temp_dir()
                .join(format!("vcs-updater-test-{:016x}", rand::random::<u64>()));
            fs::create_dir(&path).unwrap();
            Self(path)
        }
    }
    impl Drop for TestRoot {
        fn drop(&mut self) {
            for name in [
                "installer.exe",
                "installer.partial",
                "metadata.json",
                "metadata.tmp",
            ] {
                let _ = fs::remove_file(self.0.join(name));
            }
            let _ = fs::remove_dir(&self.0);
        }
    }
    #[test]
    fn cryptographic_fixture_rejects_tampered_bytes_version_and_global_comment() {
        let (mut artifact, mut bytes) = fixture();
        verify_artifact(&bytes, &artifact).unwrap();
        bytes[0] ^= 1;
        assert!(verify_artifact(&bytes, &artifact).is_err());
        bytes[0] ^= 1;
        artifact.version = "0.1.5".into();
        assert!(verify_artifact(&bytes, &artifact).is_err());
        artifact.version = "0.1.4".into();
        let raw = base64::engine::general_purpose::STANDARD
            .decode(&artifact.signature)
            .unwrap();
        let changed = String::from_utf8(raw)
            .unwrap()
            .replace("version:0.1.4", "version:0.1.5");
        artifact.signature = base64::engine::general_purpose::STANDARD.encode(changed);
        artifact.version = "0.1.5".into();
        assert!(verify_artifact(&bytes, &artifact).is_err());
    }

    // The first response deliberately stalls after half the file. A real
    // aborted reqwest response must drop immediately, preserve the prefix,
    // then a newly constructed client resumes or safely requests a full file.
    fn transport_recovery(mode: &str) {
        let root = TestRoot::new();
        let (mut artifact, payload) = fixture();
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        artifact.download_url = format!("http://{}/fixture", listener.local_addr().unwrap())
            .parse()
            .unwrap();
        let mode = mode.to_owned();
        let server_bytes = payload.clone();
        let server = std::thread::spawn(move || {
            let mut requests = Vec::new();
            let count = if mode == "resume" { 2 } else { 3 };
            for index in 0..count {
                let (mut stream, _) = listener.accept().unwrap();
                stream
                    .set_read_timeout(Some(std::time::Duration::from_secs(10)))
                    .unwrap();
                let mut request = Vec::new();
                while !request.ends_with(b"\r\n\r\n") {
                    let mut byte = [0];
                    if stream.read(&mut byte).unwrap_or(0) == 0 {
                        break;
                    }
                    request.push(byte[0]);
                }
                requests.push(String::from_utf8(request).unwrap().to_lowercase());
                let half = server_bytes.len() / 2;
                if index == 0 {
                    write!(stream, "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nETag: \"fixture\"\r\nConnection: close\r\n\r\n", server_bytes.len()).unwrap();
                    stream.write_all(&server_bytes[..half]).unwrap();
                    stream.flush().unwrap();
                    let mut byte = [0];
                    assert_eq!(
                        stream.read(&mut byte).unwrap_or(0),
                        0,
                        "cancel should close the stalled socket"
                    );
                } else if index == 1 && mode != "ignored" {
                    let etag = if mode == "changed" {
                        "different"
                    } else {
                        "fixture"
                    };
                    write!(stream, "HTTP/1.1 206 Partial Content\r\nContent-Length: {}\r\nContent-Range: bytes {}-{}/{}\r\nETag: \"{}\"\r\nConnection: close\r\n\r\n", server_bytes.len()-half, half, server_bytes.len()-1, server_bytes.len(), etag).unwrap();
                    let _ = stream.write_all(&server_bytes[half..]);
                } else {
                    write!(stream, "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nETag: \"fixture\"\r\nConnection: close\r\n\r\n", server_bytes.len()).unwrap();
                    let _ = stream.write_all(&server_bytes);
                }
            }
            requests
        });
        let cancel = AtomicBool::new(false);
        let (abort, registration) = futures_util::future::AbortHandle::new_pair();
        let saw_bytes = Arc::new(AtomicBool::new(false));
        let saw_copy = saw_bytes.clone();
        let pause_after = (payload.len() / 2) as u64;
        let channel = Channel::new(move |message| {
            if let tauri::ipc::InvokeResponseBody::Json(json) = message {
                let progress: serde_json::Value = serde_json::from_str(&json).unwrap();
                if progress["downloaded"].as_u64().unwrap() >= pause_after {
                    saw_copy.store(true, Ordering::Release);
                    abort.abort();
                }
            }
            Ok(())
        });
        let began = std::time::Instant::now();
        let result = tauri::async_runtime::block_on(futures_util::future::Abortable::new(
            download_artifact(root.0.clone(), artifact.clone(), &cancel, channel),
            registration,
        ));
        assert!(result.is_err());
        assert!(saw_bytes.load(Ordering::Acquire));
        assert!(began.elapsed() < std::time::Duration::from_secs(5));
        let paused = artifact_status(&root.0, &artifact);
        assert_eq!(paused.download_state, "paused");
        assert!(paused.resumable);
        assert_eq!(paused.downloaded, (payload.len() / 2) as u64);
        let result = tauri::async_runtime::block_on(download_artifact(
            root.0.clone(),
            artifact.clone(),
            &cancel,
            Channel::new(|_| Ok(())),
        ))
        .unwrap();
        assert_eq!(result.download_state, "downloaded");
        let requests = server.join().unwrap();
        assert!(requests[1].contains(&format!("range: bytes={}-", payload.len() / 2)));
        assert!(requests[1].contains("if-range: \"fixture\""));
        if requests.len() == 3 {
            assert!(!requests[2].contains("range:"));
        }
        // A reconstructed artifact/state reads the completed disk cache. No
        // server remains running: any accidental re-download would fail.
        let restarted = artifact.clone();
        assert_eq!(
            artifact_status(&root.0, &restarted).download_state,
            "downloaded"
        );
        let reused = tauri::async_runtime::block_on(download_artifact(
            root.0.clone(),
            restarted.clone(),
            &cancel,
            Channel::new(|_| Ok(())),
        ))
        .unwrap();
        assert_eq!(reused.download_state, "downloaded");
        assert_eq!(
            checked_artifact_bytes(&root.0, &restarted).unwrap(),
            payload
        );
        fs::write(root.0.join("installer.exe"), b"corrupt").unwrap();
        assert!(checked_artifact_bytes(&root.0, &restarted).is_err());
        assert_ne!(
            artifact_status(&root.0, &restarted).download_state,
            "downloaded"
        );
    }
    #[test]
    fn stalled_download_cancels_then_resumes_and_completed_cache_survives_restart() {
        transport_recovery("resume");
    }
    #[test]
    fn changed_entity_never_appends_and_restarts_safely() {
        transport_recovery("changed");
    }
    #[test]
    fn ignored_range_never_appends_and_restarts_safely() {
        transport_recovery("ignored");
    }
    #[test]
    fn complete_partial_is_verified_locally_without_starting_again() {
        let root = TestRoot::new();
        let (artifact, payload) = fixture();
        let mut meta = Metadata::new(&artifact);
        meta.total = Some(payload.len() as u64);
        fs::write(root.0.join("installer.partial"), &payload).unwrap();
        save_metadata(&root.0, &meta).unwrap();
        assert!(artifact_status(&root.0, &artifact).resumable);
        let result = tauri::async_runtime::block_on(download_artifact(
            root.0.clone(),
            artifact.clone(),
            &AtomicBool::new(false),
            Channel::new(|_| Ok(())),
        ))
        .unwrap();
        assert_eq!(result.download_state, "downloaded");
        assert_eq!(checked_artifact_bytes(&root.0, &artifact).unwrap(), payload);
    }
    #[test]
    fn verified_installer_survives_crash_between_rename_and_completion_metadata() {
        let root = TestRoot::new();
        let (artifact, payload) = fixture();
        let mut meta = Metadata::new(&artifact);
        meta.total = Some(payload.len() as u64);
        fs::write(root.0.join("installer.exe"), &payload).unwrap();
        save_metadata(&root.0, &meta).unwrap();
        assert_eq!(
            artifact_status(&root.0, &artifact).download_state,
            "downloaded"
        );
        assert_eq!(checked_artifact_bytes(&root.0, &artifact).unwrap(), payload);
    }
    fn meta() -> Metadata {
        Metadata {
            version: "0.1.4".into(),
            url: "unused".into(),
            signature: "unused".into(),
            etag: Some("\"same-entity\"".into()),
            total: Some(100),
            complete: false,
        }
    }
    #[test]
    fn range_requires_exact_offset_total_entity_and_remaining_size() {
        let m = meta();
        assert!(valid_range(
            &m,
            20,
            Some("\"same-entity\""),
            Some("bytes 20-99/100"),
            Some(80)
        ));
        assert!(!valid_range(
            &m,
            20,
            Some("\"new-entity\""),
            Some("bytes 20-99/100"),
            Some(80)
        ));
        assert!(!valid_range(
            &m,
            20,
            None,
            Some("bytes 20-99/100"),
            Some(80)
        ));
        assert!(!valid_range(
            &m,
            20,
            Some("\"same-entity\""),
            Some("bytes 0-99/100"),
            Some(100)
        ));
        assert!(!valid_range(
            &m,
            20,
            Some("\"same-entity\""),
            Some("bytes 20-100/101"),
            Some(81)
        ));
        assert!(!valid_range(
            &m,
            20,
            Some("\"same-entity\""),
            Some("bytes 20-99/100"),
            Some(79)
        ));
    }
    #[test]
    fn weak_or_missing_entity_and_completed_partial_are_not_resumable() {
        let mut m = meta();
        assert!(usable_partial(&m, 20));
        assert!(!usable_partial(&m, 0));
        assert!(!usable_partial(&m, 100));
        m.etag = Some("W/\"weak\"".into());
        assert!(!usable_partial(&m, 20));
        m.etag = None;
        assert!(!usable_partial(&m, 20));
        m = meta();
        m.complete = true;
        assert!(!usable_partial(&m, 20));
    }
    #[test]
    fn rejects_invalid_and_unbounded_range() {
        for bad in [
            "bytes */100",
            "bytes 100-20/100",
            "bytes 0-100/100",
            "bytes 0-10/*",
            "units 0-10/100",
            "bytes 0-300000000/300000001",
        ] {
            assert!(parse_content_range(bad).is_none(), "{bad}");
        }
    }
    #[test]
    fn signed_version_is_required_and_cannot_be_substituted() {
        assert!(verify_signed_version("timestamp:1\tversion:0.1.4", "v0.1.4").is_ok());
        assert!(verify_signed_version("timestamp:1\tversion:0.1.3", "0.1.4").is_err());
        assert!(verify_signed_version("timestamp:1\tfile:installer.exe", "0.1.4").is_err());
    }
}
