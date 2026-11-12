FROM sbtscala/scala-sbt:eclipse-temurin-25.0.3_9_1.12.13_3.8.4@sha256:02b14bfe22b46eef098e7d2c4865875006451f8f170fc2bfa2fbef0b86669780

ARG USER_ID
ARG GROUP_ID

RUN if [ "$USER_ID" != "1001" ] && [ "$USER_ID" != "0" ]; then \
        adduser -D -u $USER_ID -G $(id -gn sbtuser) -h $(eval echo ~sbtuser) newuser; \
        chown -R $USER_ID:$(id -gn sbtuser) $(eval echo ~sbtuser); \
    fi

CMD ["sbt"]
