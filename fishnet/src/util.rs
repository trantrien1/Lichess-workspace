use std::{
    cmp::{max, min},
    str,
    time::Duration,
};

use fastrand::Rng;

use crate::configure::MaxBackoff;

#[derive(Debug, Default)]
pub struct RandomizedBackoff {
    duration: Duration,
    max_backoff: MaxBackoff,
    rng: Rng,
}

impl RandomizedBackoff {
    pub fn new(max_backoff: MaxBackoff) -> RandomizedBackoff {
        RandomizedBackoff {
            duration: Duration::default(),
            max_backoff,
            rng: Rng::new(),
        }
    }

    pub fn next(&mut self) -> Duration {
        let low = 100;
        let cap = max(low, Duration::from(self.max_backoff).as_millis() as u64);
        let last = self.duration.as_millis() as u64;
        let high = 4 * max(low, last);
        let t = min(cap, self.rng.u64(low..high));
        self.duration = Duration::from_millis(t);
        self.duration
    }

    pub fn reset(&mut self) {
        self.duration = Duration::default();
    }
}

pub trait NevermindExt: Sized {
    fn nevermind(self, _msg: &str) {}
}

impl<T, E> NevermindExt for Result<T, E> {}

pub fn grow_with_and_get_mut<T, F>(vec: &mut Vec<T>, index: usize, f: F) -> &mut T
where
    F: FnMut() -> T,
{
    if vec.len() < index + 1 {
        vec.resize_with(index + 1, f);
    }
    &mut vec[index]
}

pub fn dot_thousands(n: u64) -> String {
    n.to_string()
        .as_bytes()
        .rchunks(3)
        .rev()
        .map(|s| str::from_utf8(s).expect("ascii digits"))
        .collect::<Vec<_>>()
        .join(".")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_grow_with_and_get_mut() {
        let mut vec = Vec::new();
        *grow_with_and_get_mut(&mut vec, 2, || None) = Some(2);
        *grow_with_and_get_mut(&mut vec, 0, || None) = Some(0);
        assert_eq!(vec, &[Some(0), None, Some(2)])
    }

    #[test]
    fn test_dot_thousands() {
        assert_eq!(dot_thousands(1), "1");
        assert_eq!(dot_thousands(12), "12");
        assert_eq!(dot_thousands(123), "123");
        assert_eq!(dot_thousands(1234), "1.234");
        assert_eq!(dot_thousands(12345), "12.345");
        assert_eq!(dot_thousands(123456), "123.456");
        assert_eq!(dot_thousands(1234567), "1.234.567");
    }
}
