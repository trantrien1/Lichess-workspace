use std::{
    env, fs, io,
    io::IsTerminal as _,
    path::{Path, PathBuf},
};

use shell_escape::escape;

use crate::configure::{Key, Opt};

pub fn systemd_system(opt: Opt) {
    println!("[Unit]");
    println!("Description=Fishnet client");
    println!("After=network-online.target");
    println!("Wants=network-online.target");
    println!();
    println!("[Service]");
    println!("ExecStart={} run", exec_start(Invocation::Absolute, &opt));
    println!("KillMode=mixed");
    println!("WorkingDirectory=/tmp");
    println!(
        "User={}",
        env::var("USER").unwrap_or_else(|_| "XXX".to_owned())
    );
    println!("CapabilityBoundingSet=");
    println!("PrivateTmp=true");
    println!("PrivateDevices=true");
    println!("DevicePolicy=closed");
    if opt.auto_update
        && env::current_exe()
            .expect("current exe")
            .starts_with("/usr/")
    {
        println!("ProtectSystem=false");
    } else {
        println!("ProtectSystem=full");
    }
    println!("NoNewPrivileges=true");
    println!("Restart=on-failure");
    println!();
    println!("[Install]");
    println!("WantedBy=multi-user.target");

    if io::stdout().is_terminal() {
        let command = exec_start(Invocation::Relative, &opt);
        eprintln!();
        eprintln!("# Example usage:");
        eprintln!("# {command} systemd | sudo tee /etc/systemd/system/fishnet.service");
        eprintln!("# systemctl enable fishnet.service");
        eprintln!("# systemctl start fishnet.service");
        eprintln!("# Live view of log: journalctl --unit fishnet --follow");
        eprintln!("# Prefer a user unit? {command} systemd-user");
    }
}

pub fn systemd_user(opt: Opt) {
    println!("[Unit]");
    println!("Description=Fishnet client");
    println!("After=network-online.target");
    println!("Wants=network-online.target");
    println!();
    println!("[Service]");
    println!("ExecStart={} run", exec_start(Invocation::Absolute, &opt));
    println!("KillMode=mixed");
    println!("WorkingDirectory=/tmp");
    println!("PrivateTmp=true");
    println!("DevicePolicy=closed");
    if opt.auto_update
        && env::current_exe()
            .expect("current exe")
            .starts_with("/usr/")
    {
        println!("ProtectSystem=false");
    } else {
        println!("ProtectSystem=full");
    }
    println!("Restart=on-failure");
    println!();
    println!("[Install]");
    println!("WantedBy=default.target");

    if io::stdout().is_terminal() {
        eprintln!();
        eprintln!("# Example usage:");
        eprintln!(
            "# {} systemd-user | tee ~/.config/systemd/user/fishnet.service",
            exec_start(Invocation::Relative, &opt)
        );
        eprintln!("# systemctl enable --user fishnet.service");
        eprintln!("# systemctl start --user fishnet.service");
        eprintln!("# Live view of log: journalctl --user --user-unit fishnet --follow");
    }
}

#[derive(Copy, Clone, Eq, PartialEq)]
enum Invocation {
    Absolute,
    Relative,
}

impl Invocation {
    fn exe(self) -> PathBuf {
        match self {
            Invocation::Absolute => env::current_exe().expect("current exe"),
            Invocation::Relative => env::args_os().next().expect("argv[0]").into(),
        }
    }

    fn path<P: AsRef<Path>>(self, path: P) -> PathBuf {
        match self {
            Invocation::Absolute => fs::canonicalize(path).expect("canonicalize path"),
            Invocation::Relative => path.as_ref().into(),
        }
    }
}

fn exec_start(invocation: Invocation, opt: &Opt) -> String {
    let mut builder = vec![
        escape(
            invocation
                .exe()
                .to_str()
                .expect("printable exe path")
                .into(),
        )
        .into_owned(),
    ];

    if opt.verbose.level > 0 {
        builder.push(format!("-{}", "v".repeat(usize::from(opt.verbose.level))));
    }
    if opt.auto_update {
        builder.push("--auto-update".to_owned());
    }

    if opt.no_conf {
        builder.push("--no-conf".to_owned());
    } else if opt.conf.is_some() || invocation == Invocation::Absolute {
        builder.push("--conf".to_owned());
        builder.push(
            escape(
                invocation
                    .path(opt.conf())
                    .to_str()
                    .expect("printable --conf path")
                    .into(),
            )
            .into_owned(),
        );
    }

    if let Some(ref key_file) = opt.key_file {
        builder.push("--key-file".to_owned());
        builder.push(
            escape(
                invocation
                    .path(key_file)
                    .to_str()
                    .expect("printable --key-file path")
                    .into(),
            )
            .into_owned(),
        );
    } else if let Some(Key(ref key)) = opt.key {
        builder.push("--key".to_owned());
        builder.push(escape(key.into()).into_owned());
    }

    if let Some(ref endpoint) = opt.endpoint {
        builder.push("--endpoint".to_owned());
        builder.push(escape(endpoint.to_string().into()).into_owned());
    }
    if let Some(ref cores) = opt.cores {
        builder.push("--cores".to_owned());
        builder.push(escape(cores.to_string().into()).into_owned());
    }
    if let Some(ref max_backoff) = opt.max_backoff {
        builder.push("--max-backoff".to_owned());
        builder.push(max_backoff.to_string());
    }
    if let Some(ref user_backlog) = opt.backlog.user {
        builder.push("--user-backlog".to_owned());
        builder.push(escape(user_backlog.to_string().into()).into_owned());
    }
    if let Some(ref system_backlog) = opt.backlog.system {
        builder.push("--system-backlog".to_owned());
        builder.push(escape(system_backlog.to_string().into()).into_owned());
    }

    builder.join(" ")
}
