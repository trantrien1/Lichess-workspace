# OpenShift Deployment

This document describes the steps to launch _FishNet_ on an _OpenShift 4_ cluster.

## Steps

1. Create a new project. `project.yaml` can be used.
2. Create a secret with your _FishNet_ API key. Number of CPU cores to be used can also be set here.
3. Create a new deployment using the provided `deployment.yaml` file.
4. Set the number of replicas/pods to your preference.

### 1. Create a new project

We assume, that you are already logged in to your _OCP_ cluster. For simplicity the steps shown in this document refer to the commandline, but can also be done via the _OCP GUI_.

There are two simple ways to create a new project:

- `oc new-project fishnet` (this command also changes to the project automatically)

or use the provided `project.yaml` file

- `oc apply -f project.yaml` (you need to change to the new project manually: `oc project fishnet`)

### 2. Create a Secret with your FishNet API key

Create a secret named `fishnet`:

`oc create secret generic fishnet -n fishnet --from-literal KEY="<Your_API_Key>"`

By default _FishNet_ sets the number of cores to be used to `n-1`. So if you dont want the worker to use this value, you could add a `CORES` value to the command like this:

`oc create secret generic fishnet -n fishnet --from-literal KEY="<Your_API_Key>" --from-literal CORES="1"`

### 3. Create a Deployment

Apply the provided `deployment.yaml` file like this:

`oc apply -f deployment.yaml -n fishnet`

### 4. Check and scale your Deplyoment

By default there is one _replica_ defined, which results in one _pod_ running.
You can check the _deployment_ and the _pods_ like this:

`oc get deployment -n fishnet` and `oc get pods -n fishnet`

To scale the number of _pods_ up and down use this command and provide the number of _replicas_ you want to use:

`oc scale deployment fishnet -n fishnet --replicas=<Number_of_Replicas>`
