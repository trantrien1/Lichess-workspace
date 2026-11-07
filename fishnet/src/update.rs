use std::{fmt, io, io::Write as _, time::Duration};

use futures_util::StreamExt as _;
use reqwest::Client;
use self_replace::self_replace;
use semver::Version;
use serde::Deserialize;
use tempfile::NamedTempFile;
use tokio::time::{error::Elapsed, timeout};

use crate::logger::Logger;

pub async fn auto_update(
    verbose: bool,
    client: &Client,
    logger: &Logger,
) -> Result<UpdateSuccess, UpdateError> {
    if verbose {
        logger.headline("Updating ...");
    }

    // Find relevant updates.
    logger.fishnet_info("Checking for updates (--auto-update) ...");
    let current = Version::parse(env!("CARGO_PKG_VERSION")).expect("valid package version");
    let latest = latest_release(client).await?;
    logger.debug(&format!(
        "Current release is v{}, latest is v{}",
        current, latest.version
    ));
    if latest.version <= current {
        return Ok(UpdateSuccess::UpToDate(current));
    }

    // Request download.
    logger.fishnet_info(&format!("Downloading v{} ...", latest.version));
    let mut temp_exe = NamedTempFile::with_prefix("fishnet-auto-update")?;
    let mut download = timeout(
        Duration::from_secs(30),
        client
            .get(format!(
                "https://fishnet-releases.s3.dualstack.eu-west-3.amazonaws.com/{}",
                latest.key
            ))
            .timeout(Duration::from_secs(15 * 60)) // Override default meant for small requests
            .send(),
    )
    .await??
    .error_for_status()?
    .bytes_stream();

    // Download.
    while let Some(part) = timeout(Duration::from_secs(30), download.next()).await? {
        let part = part?;
        temp_exe.write_all(&part)?;
    }
    temp_exe.flush()?;

    // Replace current executable.
    self_replace(temp_exe)?;
    Ok(UpdateSuccess::Updated(latest.version))
}

async fn latest_release(client: &Client) -> Result<Release, UpdateError> {
    let bucket: ListBucket = quick_xml::de::from_str(
        &client
            .get("https://fishnet-releases.s3.dualstack.eu-west-3.amazonaws.com/?list-type=2")
            .send()
            .await?
            .error_for_status()?
            .text()
            .await?,
    )?;

    bucket
        .contents
        .into_iter()
        .flat_map(Content::release)
        .filter(|release| release.key.contains(effective_target()))
        .max_by_key(|release| release.version.clone())
        .ok_or(UpdateError::NoReleases)
}

fn effective_target() -> &'static str {
    match env!("FISHNET_TARGET") {
        "x86_64-unknown-linux-gnu" => "x86_64-unknown-linux-musl",
        "aarch64-unknown-linux-gnu" => "aarch64-unknown-linux-musl",
        other => other,
    }
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "PascalCase")]
struct ListBucket {
    contents: Vec<Content>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "PascalCase")]
struct Content {
    key: String,
}

impl Content {
    fn release(self) -> Option<Release> {
        let (version, _filename) = self.key.split_once('/')?;
        let version = version.strip_prefix('v')?;
        Some(Release {
            version: version.parse().ok()?,
            key: self.key,
        })
    }
}

#[derive(Debug, Clone)]
struct Release {
    version: Version,
    key: String,
}

pub enum UpdateSuccess {
    Updated(Version),
    UpToDate(Version),
}

#[derive(Debug)]
pub enum UpdateError {
    NoReleases,
    Network(reqwest::Error),
    Timeout,
    Xml(quick_xml::DeError),
    Io(io::Error),
}

impl fmt::Display for UpdateError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            UpdateError::NoReleases => {
                write!(f, "auto update not supported for {}", effective_target())
            }
            UpdateError::Network(err) => write!(f, "{err}"),
            UpdateError::Timeout => f.write_str("download timed out"),
            UpdateError::Xml(err) => write!(f, "unexpected response from aws: {err}"),
            UpdateError::Io(err) => write!(f, "{err}"),
        }
    }
}

impl From<reqwest::Error> for UpdateError {
    fn from(err: reqwest::Error) -> UpdateError {
        UpdateError::Network(err)
    }
}

impl From<quick_xml::DeError> for UpdateError {
    fn from(err: quick_xml::DeError) -> UpdateError {
        UpdateError::Xml(err)
    }
}

impl From<io::Error> for UpdateError {
    fn from(err: io::Error) -> UpdateError {
        UpdateError::Io(err)
    }
}

impl From<Elapsed> for UpdateError {
    fn from(_err: Elapsed) -> UpdateError {
        UpdateError::Timeout
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_list_bucket() {
        let sample = r#"
            <?xml version="1.0" encoding="UTF-8"?>
            <ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
              <Name>fishnet-releases</Name>
              <Prefix/>
              <KeyCount>74</KeyCount>
              <MaxKeys>1000</MaxKeys>
              <IsTruncated>false</IsTruncated>
              <Contents>
                <Key>v2.6.10/fishnet-v2.6.10-aarch64-apple-darwin</Key>
                <LastModified>2023-05-01T16:27:52.000Z</LastModified>
                <ETag>"f7ed5e695e421adbf153ee35a4d46fca-6"</ETag>
                <Size>30471464</Size>
                <StorageClass>STANDARD</StorageClass>
              </Contents>
            </ListBucketResult>"#;

        let bucket: ListBucket = quick_xml::de::from_str(sample).unwrap();
        let release = bucket.contents[0].clone().release().unwrap();
        assert_eq!(release.version, Version::new(2, 6, 10));
        assert_eq!(release.key, "v2.6.10/fishnet-v2.6.10-aarch64-apple-darwin");
    }
}
