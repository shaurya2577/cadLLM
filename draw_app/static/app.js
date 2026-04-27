// v1: three.js viewer + live regenerate on stroke end (debounced).
// (Real content lives in later commits; this commit is the architectural pivot.)
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { STLLoader } from 'three/addons/loaders/STLLoader.js';
// ... canvas drawing, three.js scene setup, debounced fetch to /generate ...
